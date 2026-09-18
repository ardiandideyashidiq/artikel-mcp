"""MCP tool smoke tests: full inventory of tools, resources, and prompts.

These run in-process through `create_server` so no network and no subprocess
are involved. They verify:

- Every tool registered by `server.py` is listable with a sane input schema.
- All resources and prompts are exposed.
- A full record life-cycle (add -> update -> read -> cite -> export -> search
  -> history -> delete) works end to end via `srv.call_tool`.
- Tool-level failures (unknown tool, missing arguments, unsupported source)
  surface as errors instead of silent no-ops.
"""

import asyncio
import json

import pytest

from artikel_mcp.server import create_server
from artikel_mcp.sources import registry

EXPECTED_TOOLS = {
    "search_papers",
    "download_paper",
    "ingest_bibliography",
    "get_cached_paper",
    "get_search_history",
    "add_paper",
    "update_paper",
    "delete_paper",
    "format_citation",
    "export_paper",
    "export_bibliography",
    "scan_citations",
    "insert_citation",
    "remove_citation",
    "sync_bibliography",
    "health_check",
}


def _call(srv, name, args):
    return asyncio.run(srv.call_tool(name, args))


def _text(result) -> str:
    return result.content[0].text


def test_all_tools_registered_with_schemas(tmp_path):
    srv = create_server(db_path=str(tmp_path / "schema.db"))

    async def run():
        tools = await srv.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS

        schemas = {t.name: t.input_schema for t in tools}
        assert all("properties" in s for s in schemas.values())

        # search_papers: default limit 10 (1..200), source defaults to 'all'
        search = schemas["search_papers"]
        limit_props = search["properties"]["limit"]
        assert limit_props["default"] == 10
        assert limit_props["minimum"] == 1
        assert limit_props["maximum"] == 200
        assert search["properties"]["source"]["default"] == "all"
        assert search["properties"]["force_refresh"]["default"] is False

        # download_paper: all three address params optional, none required
        download = schemas["download_paper"]
        for field in ("url", "doi", "pdf_url"):
            assert field in download["properties"]
            assert field not in download.get("required", [])

        # add_paper: title is the only required field
        add = schemas["add_paper"]
        assert add["required"] == ["title"]

        # health_check is argument-free
        assert schemas["health_check"].get("required", []) == []
        assert not schemas["health_check"]["properties"]

    asyncio.run(run())


def test_resources_and_prompts_exposed(tmp_path):
    srv = create_server(db_path=str(tmp_path / "meta.db"))

    async def run():
        resources = {r.uri for r in await srv.list_resources()}
        templates = {r.uri_template for r in await srv.list_resource_templates()}
        prompts = {p.name for p in await srv.list_prompts()}
        assert resources == {"queries://recent"}
        assert templates == {"paper://{+key}"}
        assert prompts == {"literature_review", "summarize_paper"}

    asyncio.run(run())


def test_full_record_lifecycle_via_call_tool(tmp_path):
    srv = create_server(db_path=str(tmp_path / "lifecycle.db"))

    # 1. add_paper
    add = json.loads(
        _text(
            _call(
                srv,
                "add_paper",
                {
                    "title": "Quantum Attention Networks",
                    "authors": "Ada Lovelace and Alan Turing",
                    "abstract": "attention mechanisms in quantum circuits",
                    "publication": "JMLR",
                    "year": 2024,
                    "doi": "10.9/qa.1",
                },
            )
        )
    )
    assert add["success"] is True
    key = add["key"]

    # 2. update_paper
    updated = json.loads(
        _text(
            _call(
                srv,
                "update_paper",
                {"doi_or_key": key, "research_results": "Found a quadratic speedup."},
            )
        )
    )
    assert updated["success"] is True
    assert updated["record"]["research_results"] == "Found a quadratic speedup."

    # 3. get_cached_paper
    cached = json.loads(_text(_call(srv, "get_cached_paper", {"doi_or_key": key})))
    assert cached["found"] is True
    assert cached["paper"]["title"] == "Quantum Attention Networks"

    # 4. format_citation
    citation = json.loads(
        _text(_call(srv, "format_citation", {"doi_or_key": key, "style": "apa7"}))
    )
    assert "Lovelace" in citation["reference"]
    assert citation["in_text"]

    # 5. export_bibliography
    exported = json.loads(_text(_call(srv, "export_bibliography", {"format_type": "text"})))
    assert exported["count"] == 1
    assert "Lovelace" in exported["content"]

    # 6. search_papers from local FTS
    search = json.loads(
        _text(_call(srv, "search_papers", {"query": "quantum attention", "sources": ["local"]}))
    )
    assert search["from_local"] is True
    assert search["count"] == 1
    assert search["records"][0]["doi"] == "10.9/qa.1"

    # 7. get_search_history
    history = json.loads(_text(_call(srv, "get_search_history", {"query": "quantum", "limit": 5})))
    assert history["count"] >= 1

    # 8. delete_paper
    deleted = json.loads(_text(_call(srv, "delete_paper", {"doi_or_key": key})))
    assert deleted["success"] is True

    # 9. health_check
    health = json.loads(_text(_call(srv, "health_check", {})))
    assert health["status"] == "healthy"
    assert "crossref" in health["supported_sources"]


def test_tool_error_surface(tmp_path):
    srv = create_server(db_path=str(tmp_path / "errors.db"))

    with pytest.raises(Exception, match="Unknown tool"):
        _call(srv, "no_such_tool", {})

    with pytest.raises(Exception, match="title"):
        _call(srv, "add_paper", {})

    with pytest.raises(Exception, match="search_papers"):
        _call(srv, "search_papers", {"query": "x", "source": "sinta", "force_refresh": True})

    with pytest.raises(ValueError, match="unsupported"):
        registry.search_all("deep learning", sources=["sinta"])
