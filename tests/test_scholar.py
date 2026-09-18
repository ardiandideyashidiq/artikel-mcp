"""Tests for Google Scholar source adapter, rate limiter, and anti-captcha mechanisms."""

from __future__ import annotations

import time

import pytest

from artikel_mcp.models import PaperRecord
from artikel_mcp.query_broker import adapt
from artikel_mcp.sources import registry
from artikel_mcp.sources.base import AdapterError
from artikel_mcp.sources.scholar import (
    ScholarAdapter,
    ScholarRateLimiter,
    parse_scholar_html,
)

SAMPLE_SCHOLAR_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="gs_r gs_or gs_scl" data-cid="CLUST12345" data-did="DID12345">
  <div class="gs_ggs gs_fl">
    <div class="gs_or_ggsm">
      <a href="https://journal.example.org/download/101/50">
        <span class="gs_ctg2">[PDF]</span> example.org
      </a>
    </div>
  </div>
  <div class="gs_ri">
    <h3 class="gs_rt">
      <span class="gs_ct2">[HTML]</span>
      <a href="https://journal.example.org/view/101">
        Status Hukum Deepfake di Indonesia: Tantangan Regulasi dan Perlindungan Privasi
      </a>
    </h3>
    <div class="gs_a">
      A Prasetyo, B Santoso, R Hidayat - Jurnal Teknologi dan Hukum, 2023 - journal.example.org
    </div>
    <div class="gs_rs">
      Perkembangan kecerdasan buatan menghasilkan fenomena deepfake yang melanggar privasi.
    </div>
    <div class="gs_fl">
      <a href="/scholar?cites=987654321&amp;hl=en">Cited by 18</a>
      <a href="/scholar?q=related:CLUST12345&amp;hl=en">Related articles</a>
      <a href="/scholar?cluster=123456789&amp;hl=en">All 4 versions</a>
    </div>
  </div>
</div>

<div class="gs_r gs_or gs_scl" data-cid="BOOK98765">
  <div class="gs_ri">
    <h3 class="gs_rt">
      <span class="gs_ct1">[BOOK]</span> <a href="https://books.google.com/xyz">Hukum Siber</a>
    </h3>
    <div class="gs_a">
      D Pratama - 2022 - Penerbit Akademika
    </div>
    <div class="gs_rs">
      Buku ini membahas perkembangan regulasi siber di Indonesia.
    </div>
    <div class="gs_fl">
      <a href="/scholar?cites=555444333&amp;hl=en">Cited by 42</a>
    </div>
  </div>
</div>

<div class="gs_r gs_or gs_scl" data-cid="CITE11223">
  <div class="gs_ri">
    <h3 class="gs_rt">
      <span class="gs_ctc"><span class="gs_ct1">[CITATION]</span></span>
      Analisis Forensik Digital Deepfake Video
    </h3>
    <div class="gs_a">
      M Rizky, F Rahman - Seminar Nasional Forensik Digital, 2021
    </div>
    <div class="gs_rs">
      Studi perbandingan metode ekstraksi fitur visual untuk mendeteksi rekayasa wajah.
    </div>
    <div class="gs_fl">
      <a href="/scholar?q=related:CITE11223&amp;hl=en">Related articles</a>
    </div>
  </div>
</div>
</body>
</html>
"""

CAPTCHA_HTML = """
<!DOCTYPE html>
<html>
<head><title>Sorry...</title></head>
<body onload="e=document.getElementById('captcha');">
<div class="g-recaptcha" data-sitekey="6LfwuyUTAAAAAOAmoS0fdqijC2PbbdH4kjq62Y1b"></div>
<div>Our systems have detected unusual traffic from your computer network.</div>
</body>
</html>
"""


class FakeScholarClient:
    def __init__(
        self,
        html_content: str,
        status_code: int = 200,
        url: str = "https://scholar.google.com/scholar",
    ):
        self._html = html_content
        self.status_code = status_code
        self.url = url
        self.text = html_content

    def get_response(self, url: str, params=None, headers=None, timeout=None):
        return self

    def get(self, url: str, params=None, headers=None, timeout=None) -> bytes:
        return self._html.encode("utf-8")


def test_parse_scholar_html_success():
    records = parse_scholar_html(SAMPLE_SCHOLAR_HTML)
    assert len(records) == 3

    # Item 1: Full article with PDF galley
    r0 = records[0]
    assert r0.source == "scholar"
    assert r0.source_id == "CLUST12345"
    assert (
        r0.title
        == "Status Hukum Deepfake di Indonesia: Tantangan Regulasi dan Perlindungan Privasi"
    )
    assert r0.authors == ["A Prasetyo", "B Santoso", "R Hidayat"]
    assert r0.year == 2023
    assert "Jurnal Teknologi dan Hukum" in r0.publication
    assert r0.pdf_url == "https://journal.example.org/download/101/50"
    assert r0.url == "https://journal.example.org/view/101"
    assert "Perkembangan kecerdasan buatan" in (r0.abstract or "")
    assert r0.extra["citations_count"] == 18
    assert r0.extra["versions_count"] == 4
    assert r0.extra["cluster_id"] == "CLUST12345"

    # Item 2: Book entry
    r1 = records[1]
    assert r1.title == "Hukum Siber"
    assert r1.authors == ["D Pratama"]
    assert r1.year == 2022
    assert r1.pdf_url is None
    assert r1.extra["citations_count"] == 42

    # Item 3: Citation-only entry without anchor link
    r2 = records[2]
    assert r2.title == "Analisis Forensik Digital Deepfake Video"
    assert r2.authors == ["M Rizky", "F Rahman"]
    assert r2.year == 2021
    assert "https://scholar.google.com/scholar?cluster=CITE11223" in (r2.url or "")


def test_scholar_adapter_search_with_mock():
    client = FakeScholarClient(SAMPLE_SCHOLAR_HTML)
    limiter = ScholarRateLimiter(min_delay=0.0, max_jitter=0.0)
    adapter = ScholarAdapter(client=client, rate_limiter=limiter)

    records = adapter.search("status hukum deepfake di indonesia", limit=2)
    assert len(records) == 2
    assert records[0].title.startswith("Status Hukum Deepfake")
    assert records[1].title.startswith("Hukum Siber")


def test_scholar_adapter_captcha_triggers_circuit_breaker():
    client = FakeScholarClient(
        CAPTCHA_HTML, status_code=429, url="https://www.google.com/sorry/index"
    )
    limiter = ScholarRateLimiter(min_delay=0.0, max_jitter=0.0, cooldown=10.0)
    adapter = ScholarAdapter(client=client, rate_limiter=limiter)

    with pytest.raises(AdapterError, match="anti-bot CAPTCHA or rate-limit triggered"):
        adapter.search("deepfake law")

    # Immediate second call should fail-fast due to cooldown without querying
    with pytest.raises(AdapterError, match="cooldown active"):
        adapter.search("deepfake law")


class FlakyScholarClient:
    """Simulates a client whose 1st request triggers CAPTCHA and 2nd request succeeds."""

    def __init__(self):
        self.call_count = 0

    def get_response(self, url: str, params=None, headers=None, timeout=None):
        self.call_count += 1
        if self.call_count == 1:
            return FakeScholarClient(
                CAPTCHA_HTML, status_code=429, url="https://www.google.com/sorry/index"
            )
        return FakeScholarClient(SAMPLE_SCHOLAR_HTML, status_code=200)


def test_scholar_adapter_seamless_retry_on_captcha():
    flaky = FlakyScholarClient()
    limiter = ScholarRateLimiter(min_delay=0.0, max_jitter=0.0, cooldown=0.0)
    adapter = ScholarAdapter(client=flaky, rate_limiter=limiter)

    records = adapter.search("status hukum deepfake di indonesia", limit=2)
    assert len(records) == 2
    assert flaky.call_count == 2
    assert records[0].title.startswith("Status Hukum Deepfake")


def test_rate_limiter_pacing():
    limiter = ScholarRateLimiter(min_delay=0.05, max_jitter=0.01)
    t0 = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    elapsed = time.monotonic() - t0
    # Second acquire should have slept for at least min_delay
    assert elapsed >= 0.05


def test_query_broker_adapts_scholar_query():
    adapted = adapt("tolong carikan artikel tentang status hukum deepfake di indonesia", "scholar")
    assert adapted == "status hukum deepfake di indonesia"
    assert "tolong" not in adapted
    assert "carikan" not in adapted

    adapted_count = adapt("cari 50 jurnal tentang privasi data", "scholar")
    assert adapted_count == "privasi data"


def test_registry_integration(monkeypatch):
    assert "scholar" in registry.supported_sources()
    assert registry.is_supported("scholar")

    # Scholar is opt-in by default (scrapes an anti-bot HTML endpoint)
    defaults = registry.default_sources()
    assert "scholar" not in defaults

    monkeypatch.setenv("GOOGLE_SCHOLAR_DEFAULT", "1")
    assert "scholar" in registry.default_sources()
    monkeypatch.delenv("GOOGLE_SCHOLAR_DEFAULT")

    # Mock Scholar search so offline test stays fast and never touches network
    monkeypatch.setattr(
        ScholarAdapter,
        "search",
        lambda self, q, limit=10: [
            PaperRecord(
                source="scholar",
                source_id="cluster_123",
                title="Mocked Scholar Paper",
            )
        ],
    )
    records, errors = registry.search_all("deepfake", sources=["scholar"])
    assert len(records) == 1
    assert records[0].title == "Mocked Scholar Paper"


def test_registry_disable_scholar_default(monkeypatch):
    monkeypatch.setenv("DISABLE_SCHOLAR_DEFAULT", "1")
    defaults = registry.default_sources()
    assert "scholar" not in defaults


@pytest.mark.network
def test_scholar_live_smoke():
    adapter = ScholarAdapter()
    results = adapter.smoke("deep learning transformer")
    assert isinstance(results, list)
