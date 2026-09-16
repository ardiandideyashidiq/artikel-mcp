"""Application services: search (cache-first), download (PDF + fallback), and history."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from artikel_mcp.cache import PaperCache
from artikel_mcp.models import PaperRecord
from artikel_mcp.pdf import (
    PdfError,
    download_pdf,
    extract_markdown,
    resolve_pdf_with_unpaywall,
)
from artikel_mcp.query_broker import (
    clean_conversational_query,
    extract_identifier,
    local_adapt,
)
from artikel_mcp.sources import registry

logger = logging.getLogger("artikel_mcp.service")


def search_papers(
    cache: PaperCache,
    query: str,
    sources: list[str] | None = None,
    limit: int = 20,
    force_refresh: bool = False,
) -> dict:
    """Search academic indexes and local cache with query intelligence and persistence.

    - Strips conversational phrases ('tolong carikan...', 'find papers about...').
    - Detects explicit DOIs and arXiv IDs.
    - Serves cached FTS records when available (unless force_refresh is True).
    - Logs every search query and links results in SQLite for future querying.
    """
    t0 = time.monotonic()
    cleaned = clean_conversational_query(query)
    ident = extract_identifier(query)

    def _as_dict(record: PaperRecord, error: str | None = None) -> dict:
        data = asdict(record)
        data["dedup_key"] = record.dedup_key()
        data["error"] = error
        return data

    docs: list[dict] = []
    paper_keys: list[str] = []
    requested = sources or registry.SUPPORTED
    local_only = "local" in requested and len(requested) == 1

    # Check identifier fast-path in cache
    if ident and not force_refresh:
        val = ident["value"]
        cached = cache.get_by_key(val)
        if cached:
            docs.append(_as_dict(cached))
            paper_keys.append(cached.dedup_key())
            duration_ms = (time.monotonic() - t0) * 1000
            qid = cache.log_query(query, cleaned, ["local"], 1, True, duration_ms)
            cache.link_query_papers(qid, paper_keys)
            return {
                "records": docs,
                "from_local": True,
                "errors": [],
                "sources_queried": ["local"],
            }

    # 1) Try local FTS pass first (cache-first contract) if not force_refresh
    if not force_refresh:
        hits = cache.search(local_adapt(cleaned), limit=limit, adapted=True)
        for r in hits:
            docs.append(_as_dict(r))
            paper_keys.append(r.dedup_key())
        if docs:
            logger.info("search served %d local hits from FTS", len(docs))
            duration_ms = (time.monotonic() - t0) * 1000
            qid = cache.log_query(query, cleaned, ["local"], len(docs), True, duration_ms)
            cache.link_query_papers(qid, paper_keys)
            return {
                "records": docs,
                "from_local": True,
                "errors": [],
                "sources_queried": ["local"],
            }

    if local_only:
        duration_ms = (time.monotonic() - t0) * 1000
        qid = cache.log_query(query, cleaned, ["local"], 0, True, duration_ms)
        return {
            "records": [],
            "from_local": True,
            "errors": [],
            "sources_queried": ["local"],
        }

    # 2) Upstream pass on local miss or force_refresh
    upstream = [s for s in requested if s != "local"]
    # If user provided a specific DOI or identifier, prefer Crossref / arXiv if in upstream
    upstream_query = cleaned
    if ident and (
        (ident["type"] == "doi" and "crossref" in upstream)
        or (ident["type"] == "arxiv" and "arxiv" in upstream)
    ):
        upstream_query = ident["value"]

    records, errors = registry.search_all(upstream_query, sources=upstream, limit=limit)
    cache.upsert_many(records)
    logger.info("search persisted %d upstream records", len(records))
    for r in records:
        docs.append(_as_dict(r))
        paper_keys.append(r.dedup_key())

    duration_ms = (time.monotonic() - t0) * 1000
    qid = cache.log_query(query, cleaned, upstream, len(docs), False, duration_ms)
    cache.link_query_papers(qid, paper_keys)

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
    """Download a paper's PDF, extract clean markdown, and persist it to SQLite."""
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
            # Persist markdown into cache
            target_key = doi or pdf_url
            if target_key:
                cache.upsert_markdown(target_key, md)
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
    # Persist markdown into cache
    cache.upsert_markdown(doi, md)
    return {
        "markdown": md,
        "pdf_url": oa_url,
        "via_unpaywall": True,
        "used_fallback": used_fallback,
    }


def get_cached_paper(cache: PaperCache, key_or_doi: str) -> dict | None:
    """Retrieve full metadata and cached markdown for a paper from SQLite."""
    rec = cache.get_by_key(key_or_doi)
    if rec is None:
        return None
    data = asdict(rec)
    data["dedup_key"] = rec.dedup_key()
    return data


def get_search_history(cache: PaperCache, query: str | None = None, limit: int = 20) -> list[dict]:
    """Retrieve recent queries and results from SQLite."""
    return cache.get_recent_queries(query=query, limit=limit)
