"""Unit tests for source='all' parameter and default search including Google Scholar."""

import asyncio

from artikel_mcp.cache import PaperCache
from artikel_mcp.models import PaperRecord
from artikel_mcp.server import create_server
from artikel_mcp.service import search_papers
from artikel_mcp.sources import registry


def test_registry_search_all_parameter_normalization(monkeypatch):
    """Test registry.search_all handles None, 'all', ['all'], and single source strings."""
    called_sources: list[str] = []

    def mock_search_source(name, query, limit):
        called_sources.append(name)
        return [PaperRecord(source=name, source_id=f"{name}_1", title=f"{name} Paper")]

    monkeypatch.setattr(registry, "_search_source", mock_search_source)

    # 1. Default (None) includes all supported sources (including scholar)
    called_sources.clear()
    recs, errs = registry.search_all("test query")
    assert "scholar" in called_sources
    assert set(called_sources) == set(registry.supported_sources())
    assert len(recs) == len(registry.supported_sources())

    # 2. String "all"
    called_sources.clear()
    recs, errs = registry.search_all("test query", sources="all")
    assert "scholar" in called_sources
    assert set(called_sources) == set(registry.supported_sources())

    # 3. List ["all"]
    called_sources.clear()
    recs, errs = registry.search_all("test query", sources=["all"])
    assert "scholar" in called_sources
    assert set(called_sources) == set(registry.supported_sources())

    # 4. Single source string "scholar"
    called_sources.clear()
    recs, errs = registry.search_all("test query", sources="scholar")
    assert called_sources == ["scholar"]
    assert len(recs) == 1


def test_service_search_papers_source_all_and_default(tmp_path, monkeypatch):
    """Test service.search_papers respects source='all', sources=['all'], and default."""
    cache = PaperCache(tmp_path / "test_sources.db")

    def mock_search_all(query, sources=None, limit=10):
        # Return dummy records for whatever sources were requested
        resolved = sources or registry.default_sources()
        records = [
            PaperRecord(
                source=s,
                source_id=f"{s}_id",
                title=f"{s} Research Study on Deepfake",
                authors=["Author A"],
                year=2024,
            )
            for s in resolved
        ]
        return records, []

    monkeypatch.setattr(registry, "search_all", mock_search_all)

    # 1. Default invocation (no source/sources arg) -> queries all including scholar
    res_default = search_papers(cache, "status hukum deepfake", force_refresh=True)
    assert res_default["from_local"] is False
    assert "scholar" in res_default["sources_queried"]
    assert set(res_default["sources_queried"]) == set(registry.supported_sources())

    # 2. Explicit source="all"
    res_source_all = search_papers(cache, "status hukum deepfake", source="all", force_refresh=True)
    assert "scholar" in res_source_all["sources_queried"]
    assert set(res_source_all["sources_queried"]) == set(registry.supported_sources())

    # 3. Explicit sources=["all"]
    res_sources_all = search_papers(
        cache, "status hukum deepfake", sources=["all"], force_refresh=True
    )
    assert "scholar" in res_sources_all["sources_queried"]
    assert set(res_sources_all["sources_queried"]) == set(registry.supported_sources())

    # 4. Explicit source="scholar"
    res_scholar = search_papers(
        cache, "status hukum deepfake", source="scholar", force_refresh=True
    )
    assert res_scholar["sources_queried"] == ["scholar"]

    # 5. Explicit sources=["scholar", "crossref"]
    res_subset = search_papers(
        cache, "status hukum deepfake", sources=["scholar", "crossref"], force_refresh=True
    )
    assert set(res_subset["sources_queried"]) == {"scholar", "crossref"}

    cache.close()


def test_mcp_server_search_papers_tool_schema_and_call(tmp_path, monkeypatch):
    """Test MCP tool schema has source='all' default and works via srv.call_tool."""
    db_path = tmp_path / "mcp_test.db"
    srv = create_server(db_path=str(db_path))

    # Mock search_all to stay offline and avoid network
    def mock_search_all(query, sources=None, limit=10):
        resolved = sources or registry.default_sources()
        return [
            PaperRecord(
                source=s,
                source_id=f"{s}_key",
                title=f"Title from {s}",
                authors=["Test Author"],
            )
            for s in resolved
        ], []

    monkeypatch.setattr(registry, "search_all", mock_search_all)

    async def run():
        # Check tool input schema
        tools = await srv.list_tools()
        search_tool = next(t for t in tools if t.name == "search_papers")
        props = search_tool.input_schema["properties"]

        assert "source" in props
        assert props["source"]["default"] == "all"
        assert "sources" in props

        # 1. Call tool with default arguments (should query all sources)
        call_res = await srv.call_tool("search_papers", {"query": "cyber security law"})
        assert call_res.is_error is False

        # 2. Call tool with source="all"
        call_res_all = await srv.call_tool(
            "search_papers", {"query": "cyber security law", "source": "all"}
        )
        assert call_res_all.is_error is False

        # 3. Call tool with source="scholar"
        call_res_scholar = await srv.call_tool(
            "search_papers", {"query": "cyber security law", "source": "scholar"}
        )
        assert call_res_scholar.is_error is False

        # 4. Call tool with sources=["scholar", "arxiv"]
        call_res_custom = await srv.call_tool(
            "search_papers",
            {"query": "cyber security law", "sources": ["scholar", "arxiv"]},
        )
        assert call_res_custom.is_error is False

    asyncio.run(run())
