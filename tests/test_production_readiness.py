"""Tests verifying production-readiness hardening fixes.

Covers:
- LaTeX injection protection (-no-shell-escape and custom template safety check)
- FTS5 syntax sanitization (unbalanced quotes, colons, stars)
- SQLite WAL mode and thread-safety
- Fast citekey indexing and resolution
- Health check tool
- PDF max size protection
"""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from artikel_mcp.cache import PaperCache
from artikel_mcp.doc_citation import resolve_cached_paper, validate_safe_path
from artikel_mcp.export import render_latex, validate_export_dir
from artikel_mcp.models import PaperRecord
from artikel_mcp.pdf import PdfError, download_pdf
from artikel_mcp.proxy_engine import ProxyNode, ProxyStore
from artikel_mcp.server import create_server
from artikel_mcp.service import get_cached_paper, search_papers
from artikel_mcp.sources.arxiv import ArxivAdapter
from artikel_mcp.sources.doaj import DoajAdapter
from artikel_mcp.sources.europepmc import EuropePmcAdapter


def _sample_paper() -> PaperRecord:
    return PaperRecord(
        source="manual",
        source_id="sec-01",
        title="Security in Deep Neural Networks",
        authors=["Alice Cooper", "Bob Vance"],
        doi="10.1000/sec.01",
        year=2024,
        abstract="A paper discussing adversarial attacks and prompt injection.",
    )


def test_latex_custom_template_blocks_dangerous_directives():
    rec = _sample_paper()

    with pytest.raises(ValueError, match="prohibited LaTeX directive"):
        render_latex(rec, custom_template=r"\write18{rm -rf /} {{title}}")

    with pytest.raises(ValueError, match="prohibited LaTeX directive"):
        render_latex(rec, custom_template=r"\input{/etc/passwd} {{title}}")

    with pytest.raises(ValueError, match="prohibited LaTeX directive"):
        render_latex(rec, custom_template=r"\openin 1=secret.txt {{title}}")


def test_fts5_syntax_sanitization(tmp_path):
    cache = PaperCache(tmp_path / "fts_test.db")
    rec = _sample_paper()
    cache.upsert(rec)

    # Unbalanced quotes or symbols that would crash unescaped FTS5 MATCH
    tricky_queries = [
        'adversarial "networks',
        '"unclosed quotes',
        "neural: network*",
        'title: "deep"',
        "(((complex syntax)))",
        '"""',
        "",
        "   ",
    ]

    for q in tricky_queries:
        hits = cache.search(q)
        assert isinstance(hits, list)

    cache.close()


def test_wal_mode_and_citekey_indexing(tmp_path):
    db_path = tmp_path / "wal_test.db"
    cache = PaperCache(db_path)

    # Check WAL mode
    row = cache._conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0].lower() == "wal"

    # Insert paper and verify citekey column
    rec = _sample_paper()
    cache.upsert(rec)

    saved_row = cache._conn.execute(
        "SELECT citekey FROM papers WHERE dedup_key = ?", (rec.dedup_key(),)
    ).fetchone()
    assert saved_row is not None
    assert saved_row["citekey"] == "cooper2024"

    # Verify resolve_cached_paper finds paper by citekey directly
    found = resolve_cached_paper(cache, "cooper2024")
    assert found is not None
    assert found.title == rec.title

    cache.close()


def test_health_check_tool(tmp_path):
    srv = create_server(db_path=str(tmp_path / "health.db"))

    async def run():
        return await srv.call_tool("health_check", {})

    res = asyncio.run(run())
    assert res is not None
    assert res.is_error is False
    data = res.content[0].text
    assert "healthy" in data
    assert "supported_sources" in data


def test_pdf_max_bytes_guard():
    class BigClient:
        def get(self, url, timeout=120):
            # 51MB payload
            return b"%PDF" + b"0" * (51 * 1024 * 1024)

    with pytest.raises(PdfError, match="exceeds max allowed limit"):
        download_pdf("https://example.com/big.pdf", client=BigClient())


def test_system_path_traversal_protection(tmp_path):
    with pytest.raises(PermissionError, match="Access denied"):
        validate_safe_path("/etc/passwd")

    with pytest.raises(PermissionError, match="Access denied"):
        validate_safe_path("/bin/sh")

    with pytest.raises(PermissionError, match="Access denied"):
        validate_safe_path(Path("/usr/local/bin"))

    with pytest.raises(PermissionError, match="Access denied"):
        validate_export_dir("/etc")

    safe_file = tmp_path / "safe.bib"
    safe_file.touch()
    assert validate_safe_path(str(safe_file)) == safe_file.resolve()
    assert validate_export_dir(str(tmp_path)) == tmp_path.resolve()


def test_doaj_url_encoding():
    captured_urls = []

    class FakeClient:
        def get(self, url, params=None):
            captured_urls.append(url)
            return json.dumps({"total": 0, "results": []}).encode("utf-8")

    source = DoajAdapter(client=FakeClient())
    source.search("10.1000/182+xyz")

    assert len(captured_urls) == 1
    # Slash in DOI must be percent-encoded to prevent 404
    assert "10.1000%2F182%2Bxyz" in captured_urls[0] or "10.1000%2F182" in captured_urls[0]


def test_europepmc_pmc_prefix_deduplication():
    fake_response = {
        "hitCount": 1,
        "resultList": {
            "result": [
                {
                    "id": "12345",
                    "source": "MED",
                    "pmcid": "PMC1234567",
                    "title": "PMC Test Paper",
                    "authorString": "Author A",
                    "pubYear": "2023",
                    "isOpenAccess": "Y",
                }
            ]
        },
    }

    class FakeClient:
        def get(self, url, params=None):
            return json.dumps(fake_response).encode("utf-8")

    source = EuropePmcAdapter(client=FakeClient())
    results = source.search("PMC Test")

    assert len(results) == 1
    assert results[0].pdf_url == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/pdf/"
    assert "PMCPMC" not in (results[0].pdf_url or "")


def test_proxystore_persistent_conn_and_close(tmp_path):
    db_file = tmp_path / "proxy_test.db"
    store = ProxyStore(db_file)
    node = ProxyNode(server="127.0.0.1", port=8080, uuid="00000000-0000-0000-0000-000000000000")
    store.upsert_node(node)

    store.close()
    # Once closed, calling execute directly raises ProgrammingError
    with pytest.raises(sqlite3.ProgrammingError):
        store._conn.execute("SELECT count(*) FROM proxy_nodes")


def test_search_papers_year_filtering_and_format_mode(tmp_path):
    cache = PaperCache(tmp_path / "search_test.db")
    p1 = PaperRecord(source="manual", source_id="p1", title="Quantum Computing 2019", year=2019)
    p2 = PaperRecord(source="manual", source_id="p2", title="Quantum Computing 2022", year=2022)
    p3 = PaperRecord(source="manual", source_id="p3", title="Quantum Computing 2024", year=2024)
    cache.upsert(p1)
    cache.upsert(p2)
    cache.upsert(p3)

    # Filter with year_min=2020
    res = search_papers(cache, "Quantum", source="cache", year_min=2020)
    years = [r["year"] for r in res["records"]]
    assert 2019 not in years
    assert 2022 in years
    assert 2024 in years

    # format_mode="records" only returns records
    res_records = search_papers(cache, "Quantum", source="cache", format_mode="records")
    assert "records" in res_records
    assert "formatted_summary" not in res_records

    # format_mode="summary" only returns summary
    res_summary = search_papers(cache, "Quantum", source="cache", format_mode="summary")
    assert "records" not in res_summary
    assert "formatted_summary" in res_summary
    assert len(res_summary["formatted_summary"]) > 0

    cache.close()


def test_get_cached_paper_token_controls(tmp_path):
    cache = PaperCache(tmp_path / "cached_paper_test.db")
    rec = _sample_paper()
    long_markdown = "# Paper\n\n" + "This is a detailed analysis of neural safety.\n" * 20
    cache.upsert(rec)
    cache.upsert_markdown(rec.dedup_key(), long_markdown)

    # full_text=False removes markdown
    meta_only = get_cached_paper(cache, rec.dedup_key(), full_text=False)
    assert "markdown" not in meta_only
    assert meta_only["has_markdown"] is True

    # max_chars truncates
    capped = get_cached_paper(cache, rec.dedup_key(), full_text=True, max_chars=80)
    assert capped["is_truncated"] is True
    assert len(capped["markdown"]) < 200
    assert "Content truncated" in capped["markdown"]

    cache.close()


def test_arxiv_query_grouping():
    captured_params = []

    class FakeClient:
        def get(self, url, params=None):
            captured_params.append(params)
            return b"<feed></feed>"

    source = ArxivAdapter(client=FakeClient())
    source.search("machine learning OR deep learning")

    assert len(captured_params) == 1
    assert captured_params[0]["search_query"] == "all:(machine learning OR deep learning)"
