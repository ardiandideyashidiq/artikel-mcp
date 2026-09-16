"""Source adapter registry and unified search fan-out."""

from __future__ import annotations

import logging

from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.arxiv import ArxivAdapter
from artikel_mcp.sources.base import SourceAdapter
from artikel_mcp.sources.crossref import CrossrefAdapter
from artikel_mcp.sources.doaj import DoajAdapter
from artikel_mcp.sources.europepmc import EuropePmcAdapter
from artikel_mcp.sources.garuda import GarudaAdapter
from artikel_mcp.sources.hal import HalAdapter
from artikel_mcp.sources.pmc import PmcAdapter

logger = logging.getLogger("artikel_mcp.sources")

_REGISTRY: dict[str, type[SourceAdapter]] = {
    cls.name: cls
    for cls in (
        CrossrefAdapter,
        ArxivAdapter,
        DoajAdapter,
        EuropePmcAdapter,
        HalAdapter,
        PmcAdapter,
        GarudaAdapter,
    )
}

SUPPORTED = sorted(_REGISTRY)


def supported_sources() -> list[str]:
    return list(SUPPORTED)


def is_supported(name: str) -> bool:
    return name in _REGISTRY


def search_all(
    query: str,
    sources: list[str] | None = None,
    limit: int = 20,
) -> tuple[list[PaperRecord], list[str]]:
    """Fan out over requested sources; never let one failure abort the rest.

    Returns (records, errors) where errors is a list of per-source messages.
    """
    selected = sources or SUPPORTED
    unknown = [s for s in selected if not is_supported(s)]
    if unknown:
        raise ValueError(f"unsupported source(s): {', '.join(unknown)}")

    records: list[PaperRecord] = []
    errors: list[str] = []
    for name in selected:
        try:
            adapter = _REGISTRY[name]()
            records.extend(adapter.search(query, limit=limit))
            logger.info("source %s returned %d records", name)
        except Exception as e:  # isolation: one bad source never kills the rest
            logger.warning("source %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    return records, errors