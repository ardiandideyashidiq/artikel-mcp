"""PDF pipeline tests: download validation, cleaning, and fallback."""

import pymupdf
import pytest

from artikel_mcp.http import HttpClient
from artikel_mcp.pdf import (
    PdfError,
    clean_text,
    download_pdf,
    extract_markdown,
    resolve_pdf_with_unpaywall,
)


def _mini_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in text.split("\n"):
        page.insert_text((72, y), line)
        y += 14
    data = doc.tobytes()
    doc.close()
    return data


class FakeClient:
    """Mimics HttpClient.get(url, params=...)."""

    def __init__(self, body: bytes, *, status_error: Exception | None = None, params_seen=None):
        self._body = body
        self._status_error = status_error
        self.params_seen = params_seen

    def get(self, url, *, params=None, timeout=None):
        if self.params_seen is not None:
            self.params_seen.append((url, params))
        if self._status_error:
            raise self._status_error
        return self._body


def test_download_valid_pdf_magic():
    fake = FakeClient(b"%PDF-1.5 hello")
    body, used = download_pdf("https://x/paper.pdf", client=fake)
    assert body.startswith(b"%PDF")
    assert used is False


def test_download_rejects_non_pdf():
    fake = FakeClient(b"<html><body>not a pdf</body></html>")
    with pytest.raises(PdfError, match="non-PDF"):
        download_pdf("https://x/redirect", client=fake)


def test_extract_returns_markdown():
    text = (
        "This is a sufficiently long paragraph that exceeds the garbled\n"
        "length threshold so the primary extraction path is used.\n"
        "It keeps talking about research methods and evaluation metrics\n"
        "repeatedly until the word count is big enough to pass the check.\n"
        "Second sentence maintains the flow. Third sentence closes it out."
    )
    data = _mini_pdf(text)
    md, used = extract_markdown(data)
    assert used is False
    assert "primary extraction path" in md


def test_fallback_triggers_on_garbled():
    data = _mini_pdf("x")  # tiny text -> garbled by length heuristic
    md, used = extract_markdown(data)
    assert used is True


def test_cleaner_strips_repeated_headers_and_rejoins_hyphens():
    raw = (
        "Journal of Something\n"
        "Vol 1, 2026\n"
        "Journal of Something\n"
        "This is a long word that got hyphen-\n"
        "ated across lines.\n"
        "\n"
        "Second paragraph starts here.\n"
        "Journal of Something\n"
    )
    out = clean_text(raw)
    assert "Journal of Something" not in out
    assert "hyphenated" in out
    assert "Second paragraph starts here." in out


def test_unpaywall_requires_email(monkeypatch):
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    with pytest.raises(PdfError, match="UNPAYWALL_EMAIL"):
        resolve_pdf_with_unpaywall("10.1000/xyz")


def test_unpaywall_resolves_oa_pdf(monkeypatch, tmp_path):
    monkeypatch.setenv("UNPAYWALL_EMAIL", "me@example.com")
    payload = (
        b'{"is_oa": true, "oa_locations": ['
        b'{"url_for_pdf": "https://repo.example/p.pdf", "host_type": "repository"}]}'
    )
    seen = []
    fake = FakeClient(payload, params_seen=seen)
    url = resolve_pdf_with_unpaywall("10.1000/xyz", client=fake)
    assert url == "https://repo.example/p.pdf"
    assert seen[0][1] == {"email": "me@example.com"}


def test_unpaywall_no_oa_copy(monkeypatch):
    monkeypatch.setenv("UNPAYWALL_EMAIL", "me@example.com")
    fake = FakeClient(b'{"is_oa": false}')
    assert resolve_pdf_with_unpaywall("10.1000/xyz", client=fake) is None


def test_http_client_impersonation_fallback_on_403(monkeypatch):
    client = HttpClient(impersonate="chrome124")

    class FakeResp:
        def __init__(self, status_code: int, content: bytes):
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": "application/pdf"}

    monkeypatch.setattr(client._session, "get", lambda url, **kw: FakeResp(403, b"Forbidden"))

    class FakeFallbackSession:
        def get(self, url, **kw):
            return FakeResp(200, b"%PDF-1.4 success")

    client._fallback_sessions["safari15_5"] = FakeFallbackSession()

    res = client.get("https://example.com/protected.pdf")
    assert res == b"%PDF-1.4 success"
