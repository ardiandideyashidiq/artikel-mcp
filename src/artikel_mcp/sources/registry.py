"""Source adapter registry and unified search fan-out."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, wait

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
from artikel_mcp.sources.scholar import ScholarAdapter
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
        ScholarAdapter,
        SemanticAdapter,
    )
}

SUPPORTED = sorted(_REGISTRY)


def default_sources() -> list[str]:
    """Default sources queried during unified search fan-out.

    Google Scholar is opt-in by default because it scrapes an anti-bot protected
    HTML interface; enable it with GOOGLE_SCHOLAR_DEFAULT=1.
    DISABLE_SCHOLAR_DEFAULT=1 forces it off regardless.
    """
    scholar_on = os.getenv("GOOGLE_SCHOLAR_DEFAULT", "0") == "1"
    if os.getenv("DISABLE_SCHOLAR_DEFAULT", "0") == "1":
        scholar_on = False
    if scholar_on:
        return list(SUPPORTED)
    return [s for s in SUPPORTED if s != "scholar"]


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
    sources: list[str] | str | None = None,
    limit: int = 10,
) -> tuple[list[PaperRecord], list[str]]:
    """Fan out over requested sources concurrently; never let one failure
    abort the rest. Each source receives its source-adapted query.

    Returns (records, errors) where errors is a list of per-source messages.
    """
    if sources is None:
        selected = default_sources()
    elif isinstance(sources, str):
        selected = supported_sources() if sources.lower() == "all" else [sources]
    elif any(s.lower() == "all" for s in sources):
        selected = supported_sources()
    else:
        selected = list(sources)

    unknown = [s for s in selected if not is_supported(s)]
    if unknown:
        raise ValueError(f"unsupported source(s): {', '.join(unknown)}")

    records: list[PaperRecord] = []
    errors: list[str] = []
    timeout_sec = float(os.getenv("SEARCH_ALL_TIMEOUT", "15.0"))
    pool = ThreadPoolExecutor(max_workers=min(len(selected), 12))
    futures = {
        pool.submit(_search_source, name, _query_for(name, query), limit): name
        for name in selected
    }
    try:
        done, pending = wait(futures, timeout=timeout_sec)
        for future in done:
            name = futures[future]
            try:
                records.extend(future.result())
            except Exception as e:  # isolation: one bad source never kills the rest
                logger.warning("source %s failed: %s", name, e)
                errors.append(f"{name}: {e}")
        # A source already blocked on a slow request cannot be interrupted
        # mid-flight; the HttpClient's own timeout is what actually bounds it.
        for future in pending:
            name = futures[future]
            logger.warning("source %s timed out after %.1fs", name, timeout_sec)
            errors.append(f"{name}: timed out after {timeout_sec}s")
    finally:
        pool.shutdown(wait=False)
    return records, errors
