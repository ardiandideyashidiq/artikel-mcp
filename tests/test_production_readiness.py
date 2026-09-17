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

import pytest

from artikel_mcp.cache import PaperCache
from artikel_mcp.doc_citation import resolve_cached_paper
from artikel_mcp.export import render_latex
from artikel_mcp.models import PaperRecord
from artikel_mcp.pdf import PdfError, download_pdf
from artikel_mcp.server import create_server


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
