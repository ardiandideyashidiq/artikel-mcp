"""Tests for refined MCP capabilities:
- Conversational query cleaning & identifier detection
- Query logging in search_queries table
- query_papers junction table linking
- Full-text markdown persistence and retrieval
- get_cached_paper and get_search_history tools
- MCP resources (paper://, queries://recent) and prompts
"""

import asyncio
import json

from artikel_mcp.cache import PaperCache
from artikel_mcp.models import PaperRecord
from artikel_mcp.query_broker import clean_conversational_query, extract_identifier
from artikel_mcp.server import create_server
from artikel_mcp.service import download_paper, get_cached_paper, get_search_history, search_papers


def test_conversational_query_cleaner():
    raw1 = "tolong carikan jurnal tentang kecerdasan buatan"
    assert clean_conversational_query(raw1) == "kecerdasan buatan"

    raw2 = "cari artikel tentang machine learning"
    assert clean_conversational_query(raw2) == "machine learning"

    raw3 = "please find papers about quantum error mitigation"
    assert clean_conversational_query(raw3) == "quantum error mitigation"

    raw4 = "search for research on deepfake fraud"
    assert clean_conversational_query(raw4) == "deepfake fraud"


def test_identifier_extraction():
    doi_query = "Please find details for 10.1038/s41586-020-2649-2"
    res1 = extract_identifier(doi_query)
    assert res1 is not None
    assert res1["type"] == "doi"
    assert res1["value"] == "10.1038/s41586-020-2649-2"

    arxiv_query = "https://arxiv.org/abs/2301.12345"
    res2 = extract_identifier(arxiv_query)
    assert res2 is not None
    assert res2["type"] == "arxiv"
    assert res2["value"] == "2301.12345"


def test_query_indexing_and_history(tmp_path):
    db_file = tmp_path / "test_history.db"
    cache = PaperCache(db_file)

    # Insert a sample paper
    rec = PaperRecord(
        source="arxiv",
        source_id="2301.9999",
        title="Vision Transformers for Agriculture",
        abstract="Application of vision transformers in crop disease detection",
        doi="10.1111/agri.123",
    )
    cache.upsert(rec)

    # Execute search_papers
    result = search_papers(cache, "tolong carikan jurnal agriculture", sources=["local"])
    assert result["from_local"] is True
    assert len(result["records"]) == 1

    # Verify search_queries table
    queries = cache.get_recent_queries()
    assert len(queries) == 1
    q = queries[0]
    assert q["raw_query"] == "tolong carikan jurnal agriculture"
    assert q["cleaned_query"] == "agriculture"
    assert q["results_count"] == 1
    assert q["from_local"] is True

    # Verify query_papers junction
    papers = cache.get_papers_for_query(q["id"])
    assert len(papers) == 1
    assert papers[0].title == "Vision Transformers for Agriculture"

    # Verify get_search_history service
    hist = get_search_history(cache, query="agriculture")
    assert len(hist) == 1
    assert hist[0]["cleaned_query"] == "agriculture"

    cache.close()


def test_markdown_persistence_and_retrieval(tmp_path, monkeypatch):
    db_file = tmp_path / "test_pdf_cache.db"
    cache = PaperCache(db_file)

    # Insert paper metadata
    rec = PaperRecord(
        source="crossref",
        source_id="cr1",
        title="Deep Learning in Healthcare",
        doi="10.1000/dl-health",
        pdf_url="https://example.com/paper.pdf",
    )
    cache.upsert(rec)

    # Mock download_pdf and extract_markdown
    monkeypatch.setattr(
        "artikel_mcp.service.download_pdf",
        lambda url, client=None: (b"%PDF-1.4 dummy", False),
    )
    monkeypatch.setattr(
        "artikel_mcp.service.extract_markdown",
        lambda body, force_fallback=False: (
            "# Deep Learning in Healthcare\n\nFull text content.",
            False,
        ),
    )

    # Call download_paper
    dl_res = download_paper(cache, doi="10.1000/dl-health")
    assert "Full text content." in dl_res["markdown"]

    # Verify markdown was persisted to SQLite
    cached_paper = get_cached_paper(cache, "10.1000/dl-health")
    assert cached_paper is not None
    assert cached_paper["markdown"] == "# Deep Learning in Healthcare\n\nFull text content."

    # Verify FTS search can match against markdown content
    hits = cache.search("Healthcare")
    assert len(hits) == 1
    assert hits[0].doi == "10.1000/dl-health"

    cache.close()


def test_mcp_server_schemas_and_tools(tmp_path):
    srv = create_server(db_path=str(tmp_path / "server_test.db"))

    async def run():
        tools = await srv.list_tools()
        tool_names = {t.name for t in tools}
        assert {
            "search_papers",
            "download_paper",
            "get_cached_paper",
            "get_search_history",
        } <= tool_names
        assert "ping" not in tool_names  # removed boilerplate
        assert "word_count" not in tool_names

        # Check search_papers input schema descriptions
        search_tool = next(t for t in tools if t.name == "search_papers")
        props = search_tool.input_schema["properties"]
        assert "query" in props
        assert "description" in props["query"]
        assert "sources" in props
        assert "force_refresh" in props

        # Check resources
        resources = await srv.list_resource_templates()
        template_uris = {r.uri_template for r in resources}
        assert "paper://{+key}" in template_uris

        # Check prompts
        prompts = await srv.list_prompts()
        prompt_names = {p.name for p in prompts}
        assert {"literature_review", "summarize_paper"} <= prompt_names

    asyncio.run(run())


def test_mcp_server_tool_calls_and_resources(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tools_test.db")
    cache = PaperCache(db_path)
    cache.upsert(
        PaperRecord(
            source="arxiv",
            source_id="2401.0001",
            title="Generative Agents in Science",
            abstract="Study of generative agents for automating research workflows",
            doi="10.9999/agents.1",
            markdown="# Generative Agents\n\nFull paper body.",
        )
    )
    cache.close()

    srv = create_server(db_path=db_path)

    async def run():
        # 1. Test search_papers tool call
        search_res = await srv.call_tool(
            "search_papers",
            {"query": "generative agents", "sources": ["local"]},
        )
        assert search_res.is_error is False
        assert len(search_res.content) > 0

        # 2. Test get_cached_paper tool call
        get_res = await srv.call_tool(
            "get_cached_paper",
            {"doi_or_key": "10.9999/agents.1"},
        )
        assert get_res.is_error is False

        # 3. Test get_search_history tool call
        hist_res = await srv.call_tool(
            "get_search_history",
            {"limit": 10},
        )
        assert hist_res.is_error is False

        # 4. Test reading paper resource
        res = await srv.read_resource("paper://10.9999/agents.1")
        assert len(res) == 1
        data = json.loads(res[0].content)
        assert data["title"] == "Generative Agents in Science"
        assert data["markdown"] == "# Generative Agents\n\nFull paper body."

        # 5. Test reading recent queries resource
        q_res = await srv.read_resource("queries://recent")
        assert len(q_res) == 1
        q_data = json.loads(q_res[0].content)
        assert len(q_data) >= 1

    asyncio.run(run())


def test_natural_query_limit_extraction():
    from artikel_mcp.query_broker import extract_query_limit

    q1, lim1 = extract_query_limit("cari 50 artikel tentang machine learning")
    assert lim1 == 50
    assert q1 == "machine learning"

    q2, lim2 = extract_query_limit("tolong carikan 100 jurnal tentang perubahan iklim")
    assert lim2 == 100
    assert q2 == "perubahan iklim"

    q3, lim3 = extract_query_limit("find 30 papers on quantum computing")
    assert lim3 == 30
    assert q3 == "quantum computing"

    q4, lim4 = extract_query_limit("deep learning sebanyak 75")
    assert lim4 == 75
    assert q4 == "deep learning"

    q5, lim5 = extract_query_limit("status hukum deepfake di indonesia")
    assert lim5 is None
    assert q5 == "status hukum deepfake di indonesia"


def test_mandatory_5_fields_and_formatted_summary(tmp_path):
    from artikel_mcp.cache import PaperCache
    from artikel_mcp.models import PaperRecord
    from artikel_mcp.service import search_papers

    db = tmp_path / "fields.db"
    cache = PaperCache(db)

    # Insert a paper with abstract
    rec = PaperRecord(
        source="arxiv",
        source_id="2402.12345",
        title="Scaling Transformer Language Models",
        authors=["Alice Smith", "Bob Jones"],
        doi="10.1234/scaling",
        publication="NeurIPS 2026",
        abstract=(
            "We investigate scaling behavior. The results demonstrate that models achieve 95%."
        ),
        year=2026,
    )
    cache.upsert(rec)

    # Search
    res = search_papers(cache, "cari 50 artikel tentang scaling", sources=["local"])
    assert res["from_local"] is True
    assert res["count"] == 1
    assert "formatted_summary" in res

    p = res["records"][0]
    # Guarantee 5 fields
    assert p["title"] == "Scaling Transformer Language Models"
    assert p["authors"] == ["Alice Smith", "Bob Jones"]
    assert "Alice Smith" in p["authors_str"]
    assert p["publication"] == "NeurIPS 2026"
    assert p["url"] == "https://doi.org/10.1234/scaling"
    assert "results demonstrate" in p["research_results"]

    # Guarantee formatted block
    assert "Scaling Transformer Language Models" in p["formatted"]
    assert "NeurIPS 2026" in p["formatted"]
    assert "https://doi.org/10.1234/scaling" in p["formatted"]
    assert p["formatted"] in res["formatted_summary"]

    cache.close()


def test_schema_supports_200_limit(tmp_path):
    srv = create_server(db_path=str(tmp_path / "limit_test.db"))

    async def run():
        tools = await srv.list_tools()
        search_tool = next(t for t in tools if t.name == "search_papers")
        limit_prop = search_tool.input_schema["properties"]["limit"]
        assert limit_prop["maximum"] == 200

    asyncio.run(run())
