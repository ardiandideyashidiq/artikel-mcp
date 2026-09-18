"""Tests for source expansion (OpenAlex, PubMed, Semantic Scholar) and OpenAlex OA fallback."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from artikel_mcp.cache import PaperCache
from artikel_mcp.pdf import resolve_pdf_with_openalex
from artikel_mcp.query_broker import adapt
from artikel_mcp.service import download_paper
from artikel_mcp.sources import registry
from artikel_mcp.sources.base import AdapterError
from artikel_mcp.sources.openalex import OpenAlexAdapter, _reconstruct_abstract
from artikel_mcp.sources.pubmed import PubmedAdapter
from artikel_mcp.sources.semantic import SemanticAdapter


class FakeHttpClient:
    def __init__(self, responses: dict[str, tuple[bytes, int]]):
        self.responses = responses
        self.calls = []

    def get(self, url: str, params=None, headers=None, timeout=None) -> bytes:
        self.calls.append((url, params, headers))
        for pattern, (data, status) in self.responses.items():
            if pattern in url:
                if status >= 400:
                    from artikel_mcp.http import HttpError

                    raise HttpError(f"HTTP {status}")
                return data
        from artikel_mcp.http import HttpError

        raise HttpError(f"Unexpected URL: {url}")


def test_registry_includes_expanded_sources():
    sources = registry.supported_sources()
    assert "openalex" in sources
    assert "pubmed" in sources
    assert "semantic" in sources
    assert registry.is_supported("openalex")
    assert registry.is_supported("pubmed")
    assert registry.is_supported("semantic")


def test_query_broker_adapts_expanded_sources():
    q_oa = adapt("tolong carikan jurnal kecerdasan buatan", "openalex")
    assert "intelligence" in q_oa or "buatan" in q_oa
    q_pm = adapt("cari artikel medis", "pubmed")
    assert "medical" in q_pm or "medis" in q_pm
    q_s2 = adapt("deep learning transformer", "semantic")
    assert "transformer" in q_s2


def test_openalex_abstract_reconstruction():
    inverted = {
        "Deep": [0],
        "learning": [1],
        "is": [2],
        "a": [3],
        "powerful": [4],
        "method": [5],
    }
    assert _reconstruct_abstract(inverted) == "Deep learning is a powerful method"
    assert _reconstruct_abstract({}) is None
    assert _reconstruct_abstract(None) is None


def test_openalex_adapter_mapping():
    sample_response = {
        "results": [
            {
                "id": "https://openalex.org/W12345",
                "title": "Attention Is All You Need",
                "publication_year": 2017,
                "doi": "https://doi.org/10.5555/3295222.3295349",
                "authorships": [
                    {"author": {"display_name": "Ashish Vaswani"}},
                    {"author": {"display_name": "Noam Shazeer"}},
                ],
                "abstract_inverted_index": {
                    "The": [0],
                    "dominant": [1],
                    "sequence": [2],
                    "transduction": [3],
                    "models": [4],
                },
                "primary_location": {
                    "source": {"display_name": "NeurIPS"},
                    "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
                    "landing_page_url": "https://arxiv.org/abs/1706.03762",
                },
                "open_access": {
                    "is_oa": True,
                    "oa_url": "https://arxiv.org/pdf/1706.03762.pdf",
                },
                "cited_by_count": 120000,
            }
        ]
    }
    fake_client = FakeHttpClient(
        {"api.openalex.org/works": (json.dumps(sample_response).encode("utf-8"), 200)}
    )
    adapter = OpenAlexAdapter(client=fake_client)
    papers = adapter.search("transformer", limit=5)

    assert len(papers) == 1
    p = papers[0]
    assert p.source == "openalex"
    assert p.title == "Attention Is All You Need"
    assert p.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert p.doi == "10.5555/3295222.3295349"
    assert p.year == 2017
    assert p.publication == "NeurIPS"
    assert p.abstract == "The dominant sequence transduction models"
    assert p.pdf_url == "https://arxiv.org/pdf/1706.03762.pdf"
    assert p.extra.get("cited_by_count") == 120000


def test_pubmed_adapter_mapping():
    search_response = {"esearchresult": {"idlist": ["999001"]}}
    summary_response = {
        "result": {
            "uids": ["999001"],
            "999001": {
                "title": "CRISPR-Cas9 Therapeutics in Humans",
                "authors": [{"name": "Doudna JA"}, {"name": "Charpentier E"}],
                "pubdate": "2024 Oct 10",
                "source": "Nature Medicine",
                "articleids": [
                    {"idtype": "pubmed", "value": "999001"},
                    {"idtype": "doi", "value": "10.1038/s41591-024-0001"},
                    {"idtype": "pmc", "value": "PMC1010101"},
                ],
            },
        }
    }
    fake_client = FakeHttpClient(
        {
            "esearch.fcgi": (json.dumps(search_response).encode("utf-8"), 200),
            "esummary.fcgi": (json.dumps(summary_response).encode("utf-8"), 200),
        }
    )
    adapter = PubmedAdapter(client=fake_client)
    papers = adapter.search("crispr", limit=5)

    assert len(papers) == 1
    p = papers[0]
    assert p.source == "pubmed"
    assert p.source_id == "999001"
    assert p.title == "CRISPR-Cas9 Therapeutics in Humans"
    assert p.authors == ["Doudna JA", "Charpentier E"]
    assert p.doi == "10.1038/s41591-024-0001"
    assert p.year == 2024
    assert p.publication == "Nature Medicine"
    assert "PMC1010101" in p.pdf_url


def test_semantic_adapter_mapping_and_rate_limit():
    sample_response = {
        "data": [
            {
                "paperId": "s2_abc123",
                "title": "Foundations of Deep Learning",
                "abstract": "A thorough guide to deep learning.",
                "authors": [{"name": "Ian Goodfellow"}],
                "year": 2016,
                "venue": "MIT Press",
                "externalIds": {"DOI": "10.1000/182"},
                "openAccessPdf": {"url": "https://deeplearningbook.org/book.pdf"},
                "citationCount": 50000,
            }
        ]
    }
    fake_client = FakeHttpClient(
        {
            "api.semanticscholar.org/graph/v1/paper/search": (
                json.dumps(sample_response).encode("utf-8"),
                200,
            )
        }
    )
    adapter = SemanticAdapter(client=fake_client)
    papers = adapter.search("deep learning", limit=5)
    assert len(papers) == 1
    assert papers[0].title == "Foundations of Deep Learning"
    assert papers[0].doi == "10.1000/182"
    assert papers[0].extra.get("citation_count") == 50000

    # Rate limit test (HTTP 429) surfaces as an AdapterError, not silent [])
    rate_limited_client = FakeHttpClient(
        {"api.semanticscholar.org/graph/v1/paper/search": (b"Rate limit exceeded", 429)}
    )
    adapter_rl = SemanticAdapter(client=rate_limited_client)
    with pytest.raises(AdapterError, match="rate limited"):
        adapter_rl.search("deep learning", limit=5)


def test_resolve_pdf_with_openalex():
    sample_work = {
        "open_access": {
            "is_oa": True,
            "oa_url": "https://example.org/paper.pdf",
        },
        "primary_location": {
            "pdf_url": "https://example.org/primary.pdf",
        },
    }
    fake_client = FakeHttpClient(
        {"api.openalex.org/works/": (json.dumps(sample_work).encode("utf-8"), 200)}
    )
    url = resolve_pdf_with_openalex("10.1234/test", client=fake_client)
    assert url == "https://example.org/primary.pdf"


def test_download_paper_openalex_fallback(monkeypatch):
    cache = PaperCache(":memory:")

    # Mock direct download and OJS resolver to fail
    monkeypatch.setattr(
        "artikel_mcp.service.resolve_and_download_ojs",
        MagicMock(side_effect=RuntimeError("Not OJS")),
    )
    # Mock OpenAlex OA resolution to succeed
    monkeypatch.setattr(
        "artikel_mcp.service.resolve_pdf_with_openalex",
        MagicMock(return_value="https://openalex.org/resolved.pdf"),
    )
    # Mock download_pdf for the resolved URL
    valid_pdf_bytes = b"%PDF-1.4 dummy pdf content for testing extraction"
    monkeypatch.setattr(
        "artikel_mcp.service.download_pdf",
        MagicMock(return_value=(valid_pdf_bytes, False)),
    )
    # Mock extract_markdown
    monkeypatch.setattr(
        "artikel_mcp.service.extract_markdown",
        MagicMock(return_value=("# Extracted OpenAlex Paper\n\nContent here", False)),
    )

    result = download_paper(cache, doi="10.1234/openalex-fallback-test")
    assert result["via_openalex"] is True
    assert result["pdf_url"] == "https://openalex.org/resolved.pdf"
    assert "Extracted OpenAlex Paper" in result["markdown"]
