"""Unit and integration tests for Open Journal Systems (OJS) resolution engine."""

from __future__ import annotations

import pytest

from artikel_mcp.cache import PaperCache
from artikel_mcp.ojs import (
    build_candidate_pdf_urls,
    extract_ojs_metadata,
)
from artikel_mcp.service import download_paper, get_cached_paper
from artikel_mcp.sources.doaj import DoajAdapter

_OJS3_HTML_FIXTURE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="generator" content="Open Journal Systems 3.2.1.5">
    <meta name="citation_title" content="Deepfake and Electoral Crimes: Criminal Law Perspectives">
    <meta name="citation_author" content="Rofi Aulia Rahman">
    <meta name="citation_author" content="Rizaldy Anggriawan">
    <meta name="citation_doi" content="10.18196/iclr.v7i2.26337">
    <meta name="citation_publication_date" content="2025/01/30">
    <meta name="citation_journal_title" content="Indonesian Comparative Law Review">
    <meta name="citation_pdf_url"
          content="https://journal.umy.ac.id/index.php/iclr/article/download/26337/11722">
    <meta name="citation_abstract"
          content="Deepfake technology poses legal threats to election integrity.">
</head>
<body>
    <a class="obj_galley_link pdf" href="https://journal.umy.ac.id/index.php/iclr/article/view/26337/11722">PDF</a>
</body>
</html>
"""

_OJS2_HTML_FIXTURE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="generator" content="Open Journal Systems 2.4.8.1">
    <meta name="DC.Title" content="Determinan Kontrasepsi Jangka Panjang">
    <meta name="DC.Creator.PersonalName" content="Charles Firmansyah Pasaribu">
    <meta name="DC.Identifier.DOI" content="10.14710/jkm.v14i1.52388">
    <meta name="DC.Date.issued" content="2026/01/30">
    <meta name="DC.Source" content="Jurnal Kesehatan Masyarakat">
    <meta name="DC.Description" content="Analisis penggunaan metode kontrasepsi jangka panjang.">
</head>
<body>
    <div id="articleFullText">
        <a class="file" href="/index.php/jkm/article/download/52388/pdf">Unduh PDF</a>
    </div>
</body>
</html>
"""


def test_extract_ojs3_metadata():
    base_url = "https://journal.umy.ac.id/index.php/iclr/article/view/26337"
    meta = extract_ojs_metadata(_OJS3_HTML_FIXTURE, base_url=base_url)

    assert meta.is_ojs is True
    assert meta.doi == "10.18196/iclr.v7i2.26337"
    assert "Deepfake and Electoral Crimes" in (meta.title or "")
    assert meta.authors == ["Rofi Aulia Rahman", "Rizaldy Anggriawan"]
    assert meta.publication == "Indonesian Comparative Law Review"
    assert meta.year == 2025
    assert meta.pdf_url == "https://journal.umy.ac.id/index.php/iclr/article/download/26337/11722"
    assert "legal threats" in (meta.abstract or "")


def test_extract_ojs2_galley_fallback():
    base_url = "https://ejournal3.undip.ac.id/index.php/jkm/article/view/52388"
    meta = extract_ojs_metadata(_OJS2_HTML_FIXTURE, base_url=base_url)

    assert meta.is_ojs is True
    assert meta.doi == "10.14710/jkm.v14i1.52388"
    assert meta.title == "Determinan Kontrasepsi Jangka Panjang"
    assert meta.authors == ["Charles Firmansyah Pasaribu"]
    assert meta.year == 2026
    assert meta.pdf_url == "https://ejournal3.undip.ac.id/index.php/jkm/article/download/52388/pdf"


def test_build_candidate_pdf_urls_viewer_to_download():
    viewer_url = "https://journal.umy.ac.id/index.php/iclr/article/view/26337/11722"
    candidates = build_candidate_pdf_urls(viewer_url)

    assert len(candidates) == 2
    assert candidates[0] == "https://journal.umy.ac.id/index.php/iclr/article/download/26337/11722"
    assert candidates[1] == viewer_url


def test_doaj_adapter_extracts_doi_from_identifier_array():
    raw_item = {
        "id": "doaj-12345",
        "bibjson": {
            "title": "Deepfake and Election Law",
            "doi": None,
            "identifier": [
                {"type": "pissn", "id": "1234-5678"},
                {"type": "doi", "id": "10.18196/iclr.v7i2.26337"},
            ],
            "author": [{"name": "Rofi Aulia"}],
            "year": "2025",
            "link": [
                {
                    "type": "fulltext",
                    "url": "https://journal.umy.ac.id/index.php/iclr/article/view/26337",
                }
            ],
        },
    }

    adapter = DoajAdapter()
    rec = adapter._map_item(raw_item)
    assert rec.doi == "10.18196/iclr.v7i2.26337"
    assert rec.url == "https://doi.org/10.18196/iclr.v7i2.26337"
    assert rec.authors == ["Rofi Aulia"]


def test_download_paper_service_resolves_ojs_and_enriches_cache(tmp_path, monkeypatch):
    cache = PaperCache(tmp_path / "test_ojs.db")

    class FakeResp:
        def __init__(self, content: bytes, headers: dict | None = None, url: str = ""):
            self.content = content
            self.headers = headers or {}
            self.url = url

        @property
        def text(self) -> str:
            return self.content.decode("utf-8", errors="replace")

    def fake_get_response(url: str):
        if "article/view/26337" in url and "11722" not in url:
            return FakeResp(
                _OJS3_HTML_FIXTURE.encode("utf-8"),
                headers={"content-type": "text/html"},
                url=url,
            )
        return FakeResp(
            b"%PDF-1.4 simulated pdf text content for unit test",
            headers={"content-type": "application/pdf"},
            url=url,
        )

    class FakeClient:
        def get_response(self, url: str):
            return fake_get_response(url)

        def get(self, url: str):
            return fake_get_response(url).content

    monkeypatch.setattr("artikel_mcp.ojs.get_client", lambda: FakeClient())
    monkeypatch.setattr(
        "artikel_mcp.service.extract_markdown",
        lambda body, force_fallback=False: (
            "# Extracted OJS Full Text\n\nElection crimes and AI analysis.",
            False,
        ),
    )

    result = download_paper(
        cache,
        url="https://journal.umy.ac.id/index.php/iclr/article/view/26337",
    )

    assert result["is_ojs"] is True
    assert result["doi"] == "10.18196/iclr.v7i2.26337"
    assert "Deepfake and Electoral Crimes" in result["title"]
    assert "Extracted OJS Full Text" in result["markdown"]
    assert (
        result["pdf_url"] == "https://journal.umy.ac.id/index.php/iclr/article/download/26337/11722"
    )

    # Check persistence and retrieval from SQLite cache
    cached = get_cached_paper(cache, "10.18196/iclr.v7i2.26337")
    assert cached is not None
    assert cached["doi"] == "10.18196/iclr.v7i2.26337"
    assert cached["publication"] == "Indonesian Comparative Law Review"
    assert "Extracted OJS Full Text" in cached["markdown"]


@pytest.mark.network
def test_live_ojs_download_umy(tmp_path):
    cache = PaperCache(tmp_path / "live_ojs.db")
    target_url = "https://journal.umy.ac.id/index.php/iclr/article/view/26337"

    result = download_paper(cache, url=target_url)

    assert result["is_ojs"] is True
    assert result["doi"] == "10.18196/iclr.v7i2.26337"
    assert len(result["markdown"]) > 5000
    assert "Deepfake" in result["markdown"]
    assert "26337/11722" in result["pdf_url"]
