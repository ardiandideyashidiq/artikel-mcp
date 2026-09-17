"""Source adapter registry and unified search fan-out."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from artikel_mcp.models import PaperRecord
from artikel_mcp.query_broker import adapt
from artikel_mcp.sources.arxiv import ArxivAdapter
from artikel_mcp.sources.base import SourceAdapter
from artikel_mcp.sources.crossref import CrossrefAdapter
from artikel_mcp.sources.doaj import DoajAdapter
from artikel_mcp.sources.europepmc import EuropePmcAdapter
from artikel_mcp.sources.garuda import GarudaAdapter
from artikel_mcp.sources.hal import HalAdapter
from artikel_mcp.sources.openalex import OpenAlexAdapter
from artikel_mcp.sources.pmc import PmcAdapter
from artikel_mcp.sources.pubmed import PubmedAdapter
from artikel_mcp.sources.semantic import SemanticAdapter

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
        OpenAlexAdapter,
        PubmedAdapter,
        SemanticAdapter,
    )
}

SUPPORTED = sorted(_REGISTRY)


def supported_sources() -> list[str]:
    return list(SUPPORTED)


def is_supported(name: str) -> bool:
    return name in _REGISTRY


def _search_source(name: str, query: str, limit: int) -> list[PaperRecord]:
    logger.debug("source %s starting (query=%r)", name, query)
    adapter = _REGISTRY[name]()
    found = adapter.search(query, limit=limit)
    logger.info("source %s returned %d records", name, len(found))
    return found


def _query_for(name: str, query: str) -> str:
    """Broker-adapted query, or raw query for registry-validated adapters the
    broker does not know (e.g. injected test adapters)."""
    try:
        return adapt(query, name)
    except ValueError:
        return query


def search_all(
    query: str,
    sources: list[str] | None = None,
    limit: int = 10,
) -> tuple[list[PaperRecord], list[str]]:
    """Fan out over requested sources concurrently; never let one failure
    abort the rest. Each source receives its source-adapted query.

    Returns (records, errors) where errors is a list of per-source messages.
    """
    selected = sources or SUPPORTED
    unknown = [s for s in selected if not is_supported(s)]
    if unknown:
        raise ValueError(f"unsupported source(s): {', '.join(unknown)}")

    records: list[PaperRecord] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        futures = {
            pool.submit(_search_source, name, _query_for(name, query), limit): name
            for name in selected
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                records.extend(future.result())
            except Exception as e:  # isolation: one bad source never kills the rest
                logger.warning("source %s failed: %s", name, e)
                errors.append(f"{name}: {e}")
    return records, errors
