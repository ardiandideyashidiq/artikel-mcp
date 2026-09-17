"""Regression and verification tests for the 8 codebase review bug fixes."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from artikel_mcp.cache import PaperCache
from artikel_mcp.doc_citation import remove_citation_from_file
from artikel_mcp.export import render_latex
from artikel_mcp.models import PaperRecord
from artikel_mcp.service import download_paper, search_papers
from artikel_mcp.sources.crossref import CrossrefAdapter
from artikel_mcp.sources.europepmc import EuropePmcAdapter
from artikel_mcp.sources.pmc import PmcAdapter, _extract_doi


def test_download_paper_full_doi_url_no_keyerror(monkeypatch):
    """Bug 1: download_paper with full DOI URL should not raise KeyError: 0."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = PaperCache(Path(tmp) / "test.db")
        # Pre-seed cached paper
        rec = PaperRecord(
            source="crossref",
            source_id="10.1038/s41586-020-2649-2",
            title="Sample Nature Paper",
            doi="10.1038/s41586-020-2649-2",
            markdown="# Full Paper Content",
        )
        cache.upsert(rec)

        # Call with full URL in doi argument
        res = download_paper(cache, doi="https://doi.org/10.1038/s41586-020-2649-2")
        assert res["from_cache"] is True
        assert res["title"] == "Sample Nature Paper"
        assert res["doi"] == "10.1038/s41586-020-2649-2"


def test_export_paper_special_characters_in_title():
    """Bug 2: render_latex with special characters (%, #, {, }) in title."""
    rec = PaperRecord(
        source="manual",
        source_id="p1",
        title="Achieving 95% Accuracy & $10# Savings {Under} Special Cases",
        year=2024,
    )
    latex = render_latex(rec)
    # Check that pdftitle does not contain unescaped % or specials that break hyperref
    for line in latex.splitlines():
        if "pdftitle=" in line:
            assert "%" not in line
            assert "{" not in line.split("pdftitle=")[1].replace("{{title_plain}}", "")
            assert "#" not in line
            assert "$" not in line


def test_upsert_markdown_by_pdf_url_no_duplicate_stub():
    """Bug 3: upsert_markdown by pdf_url should update existing record without creating a stub."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = PaperCache(Path(tmp) / "test.db")
        pdf_url = "https://journal.org/article/download/123/456.pdf"
        rec = PaperRecord(
            source="ojs",
            source_id="https://journal.org/article/view/123",
            title="Real Paper Title",
            pdf_url=pdf_url,
        )
        cache.upsert(rec)
        assert len(cache.list_all()) == 1

        # Now save markdown using the PDF URL
        saved = cache.upsert_markdown(pdf_url, "# Extracted Content")
        assert saved is True
        # Must still be exactly 1 record, updated in-place (not 2 records)
        all_papers = cache.list_all()
        assert len(all_papers) == 1
        assert all_papers[0].title == "Real Paper Title"
        assert all_papers[0].markdown == "# Extracted Content"


def test_crossref_empty_date_parts_no_indexerror():
    """Bug 4: Crossref adapter with date-parts: [[]] should not raise IndexError."""
    adapter = CrossrefAdapter(client=MagicMock())
    item = {
        "title": ["Sample Crossref Title"],
        "DOI": "10.1234/crossref.1",
        "issued": {"date-parts": [[]]},
    }
    rec = adapter._map_item(item)
    assert rec.title == "Sample Crossref Title"
    assert rec.year is None


def test_pmc_extracts_doi_and_parses_messy_pubdate():
    """Bug 5: PMC adapter should extract DOI from article_ids and parse messy pubdate."""
    adapter = PmcAdapter(client=MagicMock())
    article_ids = {"doi": "10.1016/j.cell.2023.01.001", "pmcid": "PMC9999999"}
    entry = {
        "title": "PMC Cell Study",
        "articleids": [
            {"idtype": "doi", "value": "10.1016/j.cell.2023.01.001"},
            {"idtype": "pmcid", "value": "PMC9999999"},
        ],
        "pubdate": "Spring 2023",
    }

    doi = _extract_doi(article_ids, entry)
    assert doi == "10.1016/j.cell.2023.01.001"

    rec = adapter._map_entry("PMC9999999", entry)
    assert rec.doi == "10.1016/j.cell.2023.01.001"
    assert rec.year == 2023


def test_europepmc_pubyear_safe_parsing():
    """Bug 6: Europe PMC adapter should safely parse string/messy pubYear."""
    adapter = EuropePmcAdapter(client=MagicMock())
    item = {
        "title": "Europe PMC Study",
        "id": "12345",
        "pubYear": "2022-2023",
    }
    rec = adapter._map_item(item)
    assert rec.year == 2022


def test_remove_citation_does_not_corrupt_email_addresses(tmp_path):
    """Bug 7: remove_citation_from_file should not match inside email addresses."""
    cache = PaperCache(tmp_path / "test.db")
    rec = PaperRecord(
        source="manual",
        source_id="p1",
        title="Author Study",
        authors=["John Author"],
        year=2024,
    )
    cache.upsert(rec)
    citekey = "author2024"

    doc_file = tmp_path / "paper.md"
    doc_file.write_text(
        f"Contact lead researcher at user@{citekey}.com or admin@{citekey} for questions.\n"
        f"As shown in previous research @{citekey}.\n\n"
        f"## References\n\n- Existing reference\n",
        encoding="utf-8",
    )

    res = remove_citation_from_file(doc_file, citekey, cache)
    assert res["success"] is True
    assert res["removed_tokens_count"] == 1  # Only the actual citation was removed

    updated_text = doc_file.read_text(encoding="utf-8")
    assert f"user@{citekey}.com" in updated_text
    assert f"admin@{citekey}" in updated_text
    assert f"previous research @{citekey}" not in updated_text


def test_search_papers_cache_alias_handled(tmp_path):
    """Bug 8: search_papers with source='cache' should be normalized to local."""
    cache = PaperCache(tmp_path / "test.db")
    # Empty cache search with source="cache" should return 0 results cleanly, not crash
    res = search_papers(cache, "nonexistent query", source="cache")
    assert res["count"] == 0
    assert res["from_local"] is True
