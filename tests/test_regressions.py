"""Regression tests for remediation fixes (offline, no network)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from artikel_mcp import http as http_mod
from artikel_mcp.cache import PaperCache
from artikel_mcp.doc_citation import sync_file_bibliography
from artikel_mcp.export import (
    _inline_markdown_to_latex,
    markdown_to_latex,
    render_latex,
)
from artikel_mcp.http import HttpClient, HttpError, validate_request_url
from artikel_mcp.models import PaperRecord
from artikel_mcp.ojs import resolve_and_download_ojs
from artikel_mcp.pdf import PdfError, extract_markdown
from artikel_mcp.query_broker import extract_identifier
from artikel_mcp.service import search_papers
from artikel_mcp.sources.base import AdapterError
from artikel_mcp.sources.openalex import OpenAlexAdapter
from artikel_mcp.sources.pmc import PmcAdapter
from artikel_mcp.sources.scholar import ScholarAdapter, parse_scholar_html
from artikel_mcp.sources.semantic import SemanticAdapter

# ---------------------------------------------------------------------------
# doc_citation: sync preserves tail, LaTeX/plain headings, scoped whitespace
# ---------------------------------------------------------------------------


def _sync_sample_record() -> PaperRecord:
    return PaperRecord(
        source="manual",
        source_id="paper-001",
        title="Deepfake Detection in Electoral Integrity Context",
        authors=["Chiquita Thefirstly Noerman", "Aji Lukman Ibrahim"],
        doi="10.26623/julr.v7i2.8995",
        url="https://doi.org/10.26623/julr.v7i2.8995",
        publication="Jurnal USM Law Review",
        year=2024,
    )


def test_sync_preserves_appendix_after_references(tmp_path: Path):
    cache = PaperCache(str(tmp_path / "t.db"))
    cache.upsert(_sync_sample_record())
    doc = tmp_path / "paper.md"
    doc.write_text(
        "# Intro\n\nText [@noerman2024].\n\n"
        "## References\n\n[1] Old entry that must be replaced.\n\n"
        "## Appendix A\n\nRaw appendix content stays.\n",
        encoding="utf-8",
    )
    res = sync_file_bibliography(doc, cache)
    assert res["success"] is True
    content = doc.read_text(encoding="utf-8")
    assert "Appendix A" in content
    assert "Raw appendix content stays." in content
    assert "Old entry that must be replaced." not in content
    assert "noerman2024" in content or "Chiquita" in content


def test_sync_recognizes_latex_references_heading(tmp_path: Path):
    cache = PaperCache(str(tmp_path / "t2.db"))
    cache.upsert(_sync_sample_record())
    doc = tmp_path / "paper.tex"
    doc.write_text(
        "Intro text @noerman2024.\n\n"
        "\\section*{References}\n\n"
        "\\noindent Stale reference line.\n\n"
        "\\section*{Appendix}\n\n"
        "Keep this tail.\n",
        encoding="utf-8",
    )
    res = sync_file_bibliography(doc, cache, section_heading=r"\section*{References}")
    assert res["success"] is True
    content = doc.read_text(encoding="utf-8")
    assert "Appendix" in content
    assert "Keep this tail." in content
    assert "Stale reference line." not in content


def test_remove_citation_whitespace_collapse_is_line_scoped(tmp_path: Path):
    cache = PaperCache(str(tmp_path / "t3.db"))
    cache.upsert(_sync_sample_record())
    doc = tmp_path / "paper.md"
    doc.write_text(
        "Body [@noerman2024] keeps spacing here.\n\n"
        "Plain line with   double   spaces stays.\n\n"
        "```\nkeep   double   spaces inside code block\n```\n",
        encoding="utf-8",
    )
    from artikel_mcp.doc_citation import remove_citation_from_file

    res = remove_citation_from_file(doc, "noerman2024", cache)
    assert res["success"] is True
    content = doc.read_text(encoding="utf-8")
    assert "Plain line with   double   spaces stays." in content
    assert "keep   double   spaces inside code block" in content
    assert "keeps spacing here" in content


# ---------------------------------------------------------------------------
# export: token protection, links, list closure, blacklist space-form
# ---------------------------------------------------------------------------


def test_export_math_and_code_not_corrupted_by_escaping():
    out = _inline_markdown_to_latex("A $E=mc^2$ and `code_x_under` sample.")
    assert "\\_\\_MATH" not in out
    assert "$E=mc^2$" in out
    assert "\\texttt{code\\_x\\_under}" in out
    assert "A " in out


def test_export_math_double_underscore_no_collision():
    out = _inline_markdown_to_latex("Result: $\\alpha_1 + \\beta_2$ is key.")
    assert "\\_\\_MATH" not in out
    assert "\\alpha_1" in out


def test_export_link_url_escaped_and_renderable():
    out = _inline_markdown_to_latex("See [paper](https://x.example/a%20b#frag).")
    assert "\\href{https://x.example/a\\%20b\\#frag}{paper}" in out


def test_export_list_closed_before_following_paragraph():
    out = markdown_to_latex("- item one\n- item two\n\nFollow-up paragraph here.")
    assert "\\end{itemize}" in out
    assert out.index("Follow-up paragraph here.") > out.index("\\end{itemize}")


def test_export_blacklist_blocks_space_form_include(tmp_path: Path):
    rec = PaperRecord(source="m", source_id="1", title="T")
    with pytest.raises(ValueError, match="prohibited LaTeX directive"):
        render_latex(rec, custom_template=r"\input /etc/passwd {{title}}")
    with pytest.raises(ValueError, match="prohibited LaTeX directive"):
        render_latex(rec, custom_template=r"\include secret.tex {{title}}")


def test_export_dir_shared_path_sandbox(tmp_path: Path):
    monkey = pytest.MonkeyPatch()
    monkey.setenv("ARTIKEL_MCP_ALLOWED_DIR", str(tmp_path))
    try:
        from artikel_mcp.export import validate_export_dir

        inside = validate_export_dir(str(tmp_path / "sub"))
        assert inside == (tmp_path / "sub").resolve()
    finally:
        monkey.undo()
    with pytest.raises(PermissionError):
        validate_export_dir("/etc")


# ---------------------------------------------------------------------------
# http: SSRF blocks, redirect validation, raw size cap
# ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, status_code: int, content: bytes = b"", headers: dict | None = None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class FakeSession:
    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


class FakeFallback:
    def get(self, url, **kwargs):
        return FakeResp(200, b"ok")


def test_http_blocks_private_ip_urls():
    for bad in (
        "http://127.0.0.1/x",
        "http://192.168.1.10/x",
        "http://10.0.0.1/x",
        "http://0.0.0.0/x",
        "http://localhost/x",
        "http://intranet.local/x",
        "file:///etc/passwd",
    ):
        with pytest.raises(HttpError):
            validate_request_url(bad)


def test_http_accepts_public_hostnames():
    validate_request_url("https://example.com/protected.pdf")
    validate_request_url("https://scholar.google.com/scholar?q=x")
    client = HttpClient(impersonate="chrome124")
    fake = FakeSession([FakeResp(200, b"%PDF-1.4 ok")])
    client._session = fake  # type: ignore[assignment]
    assert client.get("https://example.com/paper.pdf") == b"%PDF-1.4 ok"


def test_http_blocks_redirect_to_private_ip(monkeypatch):
    client = HttpClient(impersonate="chrome124")
    fake = FakeSession(
        [
            FakeResp(302, headers={"location": "http://10.0.0.5/internal"}),
        ]
    )
    client._session = fake  # type: ignore[assignment]
    with pytest.raises(HttpError, match="private/reserved"):
        client.get("https://example.com/redirect")


def test_http_redirect_drops_params_on_303(monkeypatch):
    client = HttpClient(impersonate="chrome124")
    fake = FakeSession(
        [
            FakeResp(303, headers={"location": "https://example.com/final"}),
            FakeResp(200, b"%PDF-1.4 done"),
        ]
    )
    client._session = fake  # type: ignore[assignment]
    assert client.get("https://example.com/start", params={"q": "1"}) == b"%PDF-1.4 done"
    assert fake.calls[1][1]["params"] is None


def test_http_raw_path_enforces_size_cap(monkeypatch):
    monkeypatch.setattr(http_mod, "MAX_RESPONSE_BYTES", 200)
    client = HttpClient(impersonate="chrome124")
    fake = FakeSession([FakeResp(200, b"x" * 5000)])
    client._session = fake  # type: ignore[assignment]
    with pytest.raises(HttpError, match="50MB"):
        client.get_response("https://example.com/big")


# ---------------------------------------------------------------------------
# ojs: content-type-only PDF rejected
# ---------------------------------------------------------------------------


def test_ojs_rejects_content_type_only_pdf():
    class FakeClient:
        def get_response(self, url: str):
            return FakeResp(
                200,
                b"<html><body>not a pdf</body></html>",
                {"content-type": "application/pdf"},
            )

        def get(self, url: str):
            return self.get_response(url).content

    with pytest.raises(PdfError, match="content-type claims PDF but body is not a PDF"):
        resolve_and_download_ojs(
            "https://journal.example.id/article/view/9000", client=FakeClient()
        )


# ---------------------------------------------------------------------------
# pdf: exception triggers fallback; page range validated
# ---------------------------------------------------------------------------


def _mini_pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in text.split("\n"):
        page.insert_text((72, y), line)
        y += 14
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_primary_exception_triggers_fallback(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("pymupdf exploded")

    monkeypatch.setattr("artikel_mcp.pdf._extract_with_pymupdf", boom)
    md, used = extract_markdown(_mini_pdf_bytes("fallback text content line"))
    assert used is True
    assert md


def test_pdf_both_extractors_fail_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("exploded")

    monkeypatch.setattr("artikel_mcp.pdf._extract_with_pymupdf", boom)
    monkeypatch.setattr("artikel_mcp.pdf._extract_with_pymupdf4llm", boom)
    with pytest.raises(PdfError, match="both PDF extraction paths failed"):
        extract_markdown(_mini_pdf_bytes("x"))


def test_pdf_invalid_page_range_raises():
    with pytest.raises(PdfError, match="invalid page range"):
        extract_markdown(_mini_pdf_bytes("hello"), page_start=4, page_end=2)


# ---------------------------------------------------------------------------
# cache/service: arXiv fast-path and LIKE escaping
# ---------------------------------------------------------------------------


def test_arxiv_stub_roundtrip_and_service_fast_path(tmp_path: Path):
    cache = PaperCache(str(tmp_path / "arxiv.db"))
    cache.upsert_markdown("arxiv:1706.03762", "# Attention Is All You Need\n\nFull text.")
    rec = cache.get_by_key("1706.03762")
    assert rec is not None
    assert rec.source == "arxiv"
    assert rec.dedup_key() == "arxiv:1706.03762"

    res = search_papers(cache, "1706.03762")
    assert res["count"] == 1
    assert res["from_local"] is True


def test_cache_like_pattern_escaped(tmp_path: Path):
    cache = PaperCache(str(tmp_path / "like.db"))
    cache.log_query("50% of papers cited", "50% of papers cited", ["local"], 1, True)
    cache.log_query("plain query", "plain query", ["local"], 1, True)

    rows = cache.get_recent_queries("50%")
    assert len(rows) == 1
    assert rows[0]["raw_query"] == "50% of papers cited"

    rows = cache.get_recent_queries("plain_query")
    assert rows == []


# ---------------------------------------------------------------------------
# scholar: publication parsing and transport retry
# ---------------------------------------------------------------------------

SCHOLAR_MINI = """
<div class="gs_r gs_or gs_scl" data-cid="BOOK42">
  <div class="gs_ri">
    <h3 class="gs_rt"><a href="https://books.google.com/xyz">Hukum Siber</a></h3>
    <div class="gs_a">D Pratama - 2022 - Penerbit Akademika</div>
  </div>
</div>
"""


def test_scholar_publication_with_missing_venue():
    recs = parse_scholar_html(SCHOLAR_MINI)
    assert len(recs) == 1
    assert recs[0].year == 2022
    assert recs[0].publication == "Penerbit Akademika"


class FlakyTransportClient:
    def __init__(self):
        self.calls = 0

    def get_response(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise AdapterError("network partitioned")
        return _OkScholarPage()


class _OkScholarPage:
    status_code = 200
    url = "https://scholar.google.com/scholar"
    text = SCHOLAR_MINI


def test_scholar_transport_failure_retries(monkeypatch):
    from artikel_mcp.sources.scholar import ScholarRateLimiter

    flaky = FlakyTransportClient()
    limiter = ScholarRateLimiter(min_delay=0.0, max_jitter=0.0, cooldown=0.0)
    adapter = ScholarAdapter(client=flaky, rate_limiter=limiter)
    records = adapter.search("hukum siber", limit=2)
    assert flaky.calls == 2
    assert len(records) == 1
    assert records[0].title == "Hukum Siber"


# ---------------------------------------------------------------------------
# pmc: idtype "pmc" honored, no fabricated abstract
# ---------------------------------------------------------------------------


class _FakeHttp:
    def __init__(self, search_body: bytes, summary_body: bytes):
        self._search = search_body
        self._summary = summary_body

    def get(self, url: str, params=None, headers=None, timeout=None) -> bytes:
        if "esearch.fcgi" in url:
            return self._search
        return self._summary


def test_pmc_accepts_pmc_idtype_and_null_abstract():
    import json

    search = {"esearchresult": {"idlist": ["8001"]}}
    summary = {
        "result": {
            "uids": ["8001"],
            "8001": {
                "title": "Election Integrity Study",
                "pubdate": "2023 Jan",
                "fulljournalname": "Jurnal Ilmu Hukum",
                "articleids": [
                    {"idtype": "pubmed", "value": "9999"},
                    {"idtype": "pmc", "value": "PMC8008123"},
                    {"idtype": "doi", "value": "10.1000/xx.1"},
                ],
            },
        }
    }
    adapter = PmcAdapter(
        client=_FakeHttp(  # type: ignore[arg-type]
            json.dumps(search).encode("utf-8"), json.dumps(summary).encode("utf-8")
        )
    )
    papers = adapter.search("election integrity", limit=2)
    assert len(papers) == 1
    p = papers[0]
    assert p.source_id == "PMC8008123"
    assert p.pdf_url == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8008123/pdf/"
    assert p.abstract is None


# ---------------------------------------------------------------------------
# sources: 429/503 surface as AdapterError; DOI balanced parens; bib year
# ---------------------------------------------------------------------------


def test_semantic_429_surfaces_as_error():
    class RateLimited:
        def get(self, url, params=None, headers=None, timeout=None):
            raise HttpError("HTTP 429")

    with pytest.raises(AdapterError, match="rate limited"):
        SemanticAdapter(client=RateLimited()).search("deep learning")


def test_openalex_503_surfaces_as_error():
    class Unavailable:
        def get(self, url, params=None, headers=None, timeout=None):
            raise HttpError("HTTP 503")

    with pytest.raises(AdapterError, match="unavailable"):
        OpenAlexAdapter(client=Unavailable()).search("deep learning")


def test_doi_regex_balanced_parens_and_trailing_punct():
    ident = extract_identifier("Paper doi 10.26623/julr.v7i2.8995.) follows")
    assert ident == {"type": "doi", "value": "10.26623/julr.v7i2.8995"}

    legacy = "legacy 10.1002/(SICI)1097-010X(19960201)265:2<180::AID-JEZ9>3.0.CO;2-3"
    ident = extract_identifier(legacy)
    assert ident is not None
    assert ident["value"].startswith("10.1002/(SICI)1097-010X(19960201)265:2")


def test_bib_year_regex_rejects_five_digit_year(tmp_path: Path):
    from artikel_mcp.bib import parse_bib_file

    bib = tmp_path / "bad.bib"
    bib.write_text(
        "@article{key1,\n title = {Broken Year},\n year = {20241},\n}\n"
        "@article{key2,\n title = {Good Year},\n year = {2020},\n}\n",
        encoding="utf-8",
    )
    records = parse_bib_file(bib)
    by_title = {r.title: r for r in records}
    assert by_title["Broken Year"].year is None
    assert by_title["Good Year"].year == 2020
