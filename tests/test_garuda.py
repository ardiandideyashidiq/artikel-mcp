"""Garuda scraper tests, offline (no live page needed)."""

import pytest

from artikel_mcp.sources.base import AdapterError
from artikel_mcp.sources.garuda import GarudaAdapter


class FakeClient:
    def __init__(self, html: str):
        self._html = html

    def get(self, url, **kwargs):
        return self._html.encode()


DUMMY_HTML = """
<div class="article-item">
<a class="title-article" href="/documents/detail/6572265"><xmp>Deep Learning Study</xmp></a>
<a class="author-article" href="/author/view/11021836"><xmp>Bagas Asmara</xmp></a>;
<br>
<xmp class="subtitle-article">Santhet Vol 10 No 4 (2026): Santhet</xmp>
<p class="action-article">
<a class="title-citation" href="https://ejournal.example/index.php/j/article/download/8963/5638">Download</a>
</p>
</div>
"""


def test_parses_live_page_shape():
    ad = GarudaAdapter(client=FakeClient(DUMMY_HTML))
    recs = ad.search("deep learning")
    assert len(recs) == 1
    r = recs[0]
    assert r.source == "garuda"
    assert r.source_id == "6572265"
    assert r.title == "Deep Learning Study"
    assert r.authors == ["Bagas Asmara"]
    assert r.year == 2026
    assert "download/8963/5638" in r.pdf_url


def test_fail_fast_on_unrecognized_structure():
    ad = GarudaAdapter(client=FakeClient("<html><body>captcha page</body></html>"))
    with pytest.raises(AdapterError, match="unrecognized"):
        ad.search("deep learning")