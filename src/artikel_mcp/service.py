"""Application services: search (cache-first) and download (PDF + fallback)."""

from __future__ import annotations

import logging
from dataclasses import asdict

from artikel_mcp.cache import PaperCache
from artikel_mcp.models import PaperRecord
from artikel_mcp.pdf import (
    PdfError,
    download_pdf,
    extract_markdown,
    resolve_pdf_with_unpaywall,
)
from artikel_mcp.sources import registry

logger = logging.getLogger("artikel_mcp.service")

# ponytail: synchronous PDF download/extract inside the MCP server blocks the
#           stdio loop; fine for single-user use. Add a queue worker if
#           throughput ever matters.


def search_papers(
    cache: PaperCache,
    query: str,
    sources: list[str] | None = None,
    limit: int = 20,
) -> dict:
    """Cache-first search. Local FTS hits served without upstream."""

    def _as_dict(record: PaperRecord, error: str | None = None) -> dict:
        data = asdict(record)
        data["error"] = error
        return data

    docs: list[dict] = []
    requested = sources or registry.SUPPORTED
    local_only = "local" in requested and len(requested) == 1

    # 1) always try the local FTS pass first (cache-first contract)
    hits = cache.search(query, limit=limit)
    for r in hits:
        docs.append(_as_dict(r))
    if docs:
        logger.info("search served %d local hits from FTS", len(docs))
        return {
            "records": docs,
            "from_local": True,
            "errors": [],
            "sources_queried": ["local"],
        }

    if local_only:
        return {
            "records": [],
            "from_local": True,
            "errors": [],
            "sources_queried": ["local"],
        }

    # 2) upstream pass on local miss
    upstream = [s for s in requested if s != "local"]
    records, errors = registry.search_all(query, sources=upstream, limit=limit)
    cache.upsert_many(records)
    logger.info("search persisted %d upstream records", len(records))
    for r in records:
        docs.append(_as_dict(r))
    return {
        "records": docs,
        "from_local": False,
        "errors": errors,
        "sources_queried": upstream,
    }


def download_paper(
    cache: PaperCache,
    *,
    doi: str | None = None,
    pdf_url: str | None = None,
    force_fallback: bool = False,
) -> dict:
    """Download a paper's PDF and extract text; Unpaywall on paywall."""

    if not doi and not pdf_url:
        raise ValueError("need at least one of doi or pdf_url")

    # prefer a cached record for direct PDF link
    if doi and not pdf_url:
        cached = cache.get_by_key(doi.lower())
        if cached and cached.pdf_url:
            pdf_url = cached.pdf_url
            logger.debug("used cached pdf_url %s", pdf_url[:60])

    if pdf_url:
        try:
            body, _used_unpaywall = download_pdf(pdf_url)
            md, used_fallback = extract_markdown(body, force_fallback=force_fallback)
            return {
                "markdown": md,
                "pdf_url": pdf_url,
                "via_unpaywall": False,
                "used_fallback": used_fallback,
            }
        except Exception as e:
            logger.info("direct pdf failed (%s); trying unpaywall", e)

    if not doi:
        raise PdfError("direct download failed and no DOI for Unpaywall fallback")

    oa_url = resolve_pdf_with_unpaywall(doi)
    if oa_url is None:
        raise PdfError("no open-access copy available for this DOI")
    body, _ = download_pdf(oa_url)
    md, used_fallback = extract_markdown(body, force_fallback=force_fallback)
    return {
        "markdown": md,
        "pdf_url": oa_url,
        "via_unpaywall": True,
        "used_fallback": used_fallback,
    }
