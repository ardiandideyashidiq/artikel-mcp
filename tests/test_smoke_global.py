"""Live smoke tests for source adapters against real endpoints."""

import pytest

from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.arxiv import ArxivAdapter
from artikel_mcp.sources.crossref import CrossrefAdapter
from artikel_mcp.sources.doaj import DoajAdapter
from artikel_mcp.sources.europepmc import EuropePmcAdapter
from artikel_mcp.sources.garuda import GarudaAdapter
from artikel_mcp.sources.hal import HalAdapter
from artikel_mcp.sources.pmc import PmcAdapter
from artikel_mcp.sources.registry import search_all, supported_sources

pytestmark = pytest.mark.network


@pytest.mark.parametrize(
    "adapter_cls",
    [
        CrossrefAdapter,
        ArxivAdapter,
        DoajAdapter,
        EuropePmcAdapter,
        HalAdapter,
        PmcAdapter,
        GarudaAdapter,
    ],
)
def test_global_adapter_smoke(adapter_cls):
    recs = adapter_cls().smoke()
    assert recs, "expected at least one record from live API"
    for r in recs:
        assert isinstance(r, PaperRecord)
        assert r.source
        assert r.source_id
        assert r.title


def test_unsupported_source_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        search_all("deep learning", sources=["sinta"])
    assert "crossref" in supported_sources()
    assert "garuda" in supported_sources()
