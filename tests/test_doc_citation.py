"""Unit tests for document citation manager: scanning, CRUD, and bibliography sync."""

from pathlib import Path

from artikel_mcp.cache import PaperCache
from artikel_mcp.doc_citation import (
    extract_citekey,
    insert_citation_in_file,
    remove_citation_from_file,
    scan_file_citations,
    sync_file_bibliography,
)
from artikel_mcp.models import PaperRecord
from artikel_mcp.service import (
    insert_document_citation,
    remove_document_citation,
    scan_document_citations,
    sync_document_bibliography,
)


def _sample_record() -> PaperRecord:
    return PaperRecord(
        source="manual",
        source_id="paper-001",
        title="Deepfake Detection in Electoral Integrity Context",
        authors=["Chiquita Thefirstly Noerman", "Aji Lukman Ibrahim"],
        doi="10.26623/julr.v7i2.8995",
        url="https://doi.org/10.26623/julr.v7i2.8995",
        publication="Jurnal USM Law Review",
        year=2024,
        abstract="This study investigates deepfake regulation and electoral integrity.",
        research_results="The results explain that Indonesia needs explicit regulation.",
    )


def _second_record() -> PaperRecord:
    return PaperRecord(
        source="manual",
        source_id="paper-002",
        title="Blockchain Consensus Protocols in High-Latency Networks",
        authors=["Satoshi Nakamoto"],
        doi="10.1000/182",
        url="https://doi.org/10.1000/182",
        publication="Journal of Cryptography",
        year=2021,
    )


def test_extract_citekey():
    rec1 = _sample_record()
    assert extract_citekey(rec1) == "noerman2024"

    rec2 = _second_record()
    assert extract_citekey(rec2) == "nakamoto2021"

    rec_no_author = PaperRecord(
        source="manual",
        source_id="p3",
        title="Unknown Paper",
        authors=[],
        year=2020,
    )
    assert extract_citekey(rec_no_author) == "item2020"


def test_scan_file_citations(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cache = PaperCache(str(db_path))
    rec1 = _sample_record()
    cache.upsert(rec1)

    doc = tmp_path / "paper.md"
    doc.write_text(
        "# Introduction\n\n"
        "Technological advances have raised concerns [@noerman2024].\n"
        "Furthermore, @noerman2024 noted election issues.\n"
        "An unknown author argued another point [@smith2023].\n"
        "Direct DOI mention 10.26623/julr.v7i2.8995 here.\n\n"
        "## References\n\n"
        "Should not parse citations in this section [@ignored2020].\n",
        encoding="utf-8",
    )

    result = scan_file_citations(doc, cache)
    assert result["total_citations_found"] >= 2
    assert "smith2023" in result["unresolved_keys"]
    assert "ignored2020" not in result["raw_citations"]
    assert result["resolved_count"] == 1
    assert result["resolved_papers"][0]["citekey"] == "noerman2024"


def test_insert_citation_in_file(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cache = PaperCache(str(db_path))
    rec = _sample_record()
    cache.upsert(rec)

    doc = tmp_path / "paper.md"
    doc.write_text(
        "# Introduction\n\nRecent legal studies have emerged.\n",
        encoding="utf-8",
    )

    res = insert_citation_in_file(
        doc,
        "noerman2024",
        cache,
        line_number=3,
        marker_format="pandoc",
        style="apa7",
        auto_sync=True,
    )

    assert res["success"] is True
    assert res["inserted_token"] == "[@noerman2024]"
    assert res["auto_synced"] is True

    content = doc.read_text(encoding="utf-8")
    assert "[@noerman2024]" in content
    assert "## References" in content
    assert "Noerman, C. T., & Ibrahim, A. L. (2024)" in content

    # Companion .bib should have been created
    bib_file = tmp_path / "paper.bib"
    assert bib_file.exists()
    assert "@article{noerman2024" in bib_file.read_text(encoding="utf-8")


def test_remove_citation_from_file(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cache = PaperCache(str(db_path))
    rec1 = _sample_record()
    rec2 = _second_record()
    cache.upsert(rec1)
    cache.upsert(rec2)

    doc = tmp_path / "paper.md"
    doc.write_text(
        "# Background\n\n"
        "Deepfake regulation is discussed in [@noerman2024].\n"
        "Consensus algorithms are explored in [@nakamoto2021].\n\n"
        "## References\n\n"
        "Placeholder\n",
        encoding="utf-8",
    )

    # Initial sync
    sync_file_bibliography(doc, cache, style="apa7")
    content_before = doc.read_text(encoding="utf-8")
    assert "Noerman" in content_before
    assert "Nakamoto" in content_before

    # Remove noerman2024
    res = remove_citation_from_file(doc, "noerman2024", cache, sync_bib=True, style="apa7")
    assert res["success"] is True
    assert res["removed_tokens_count"] >= 1

    content_after = doc.read_text(encoding="utf-8")
    assert "[@noerman2024]" not in content_after
    assert "[@nakamoto2021]" in content_after
    assert "Noerman" not in content_after
    assert "Nakamoto" in content_after


def test_sync_file_bibliography_styles(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cache = PaperCache(str(db_path))
    rec = _sample_record()
    cache.upsert(rec)

    doc = tmp_path / "draft.md"
    doc.write_text(
        "# Paper Title\n\nAccording to [@noerman2024], new safeguards are required.\n",
        encoding="utf-8",
    )

    # Sync with IEEE style
    sync_file_bibliography(doc, cache, style="ieee", companion_bib=True)
    ieee_text = doc.read_text(encoding="utf-8")
    assert "[1] C. T. Noerman and A. L. Ibrahim" in ieee_text

    # Switch to Chicago Author-Date style
    sync_file_bibliography(doc, cache, style="chicago", companion_bib=True)
    chicago_text = doc.read_text(encoding="utf-8")
    assert "Noerman, Chiquita Thefirstly, and Aji Lukman Ibrahim. 2024." in chicago_text


def test_service_document_citations(tmp_path: Path):
    db_path = tmp_path / "test.db"
    cache = PaperCache(str(db_path))
    rec1 = _sample_record()
    rec2 = _second_record()
    cache.upsert(rec1)
    cache.upsert(rec2)

    doc = tmp_path / "test_doc.md"
    doc.write_text("# Overview\n\nInitial text line.\n", encoding="utf-8")

    # 1. Insert through service
    res_ins = insert_document_citation(
        cache,
        str(doc),
        rec1.doi,
        marker_format="pandoc",
        style="apa7",
        auto_sync=True,
    )
    assert res_ins["success"] is True

    # 2. Scan through service
    res_scan = scan_document_citations(cache, str(doc))
    assert res_scan["resolved_count"] == 1
    assert res_scan["resolved_papers"][0]["doi"] == rec1.doi

    # 3. Sync through service with different style
    res_sync = sync_document_bibliography(cache, str(doc), style="mla9")
    assert res_sync["success"] is True
    assert "Noerman, Chiquita Thefirstly" in doc.read_text(encoding="utf-8")

    # 4. Remove through service
    res_rem = remove_document_citation(cache, str(doc), rec1.doi, sync_bib=True)
    assert res_rem["success"] is True
    assert "*No cited papers found in document.*" in doc.read_text(encoding="utf-8")


def test_mcp_server_doc_tools(tmp_path: Path):
    import asyncio

    from artikel_mcp.server import create_server

    server = create_server(str(tmp_path / "server.db"))
    tools = asyncio.run(server.list_tools())
    tool_names = [t.name for t in tools]
    assert "scan_citations" in tool_names
    assert "insert_citation" in tool_names
    assert "remove_citation" in tool_names
    assert "sync_bibliography" in tool_names
