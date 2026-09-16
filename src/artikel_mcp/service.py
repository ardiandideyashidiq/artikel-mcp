"""Application services: search (cache-first), download (PDF + fallback), and history."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from artikel_mcp.cache import PaperCache
from artikel_mcp.models import PaperRecord
from artikel_mcp.ojs import OjsArticleMetadata, resolve_and_download_ojs
from artikel_mcp.pdf import (
    PdfError,
    download_pdf,
    extract_markdown,
    resolve_pdf_with_unpaywall,
)
from artikel_mcp.query_broker import (
    extract_identifier,
    extract_query_limit,
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
    - Detects explicit count requests ('cari 50 artikel tentang...').
    - Detects explicit DOIs and arXiv IDs.
    - Guarantees 5 mandatory fields: title, authors, publication, DOI/link, and research results.
    - Serves cached FTS records when available (unless force_refresh is True).
    - Logs every search query and links results in SQLite for future querying.
    """
    t0 = time.monotonic()
    cleaned, natural_limit = extract_query_limit(query)
    if natural_limit is not None and limit == 20:
        limit = natural_limit
    ident = extract_identifier(cleaned)

    def _as_dict(record: PaperRecord, idx: int = 1, error: str | None = None) -> dict:
        data = asdict(record)
        data["dedup_key"] = record.dedup_key()
        data["authors_str"] = ", ".join(record.authors) if record.authors else "Unknown Authors"
        data["url"] = record.url or (
            f"https://doi.org/{record.doi}"
            if record.doi
            else f"https://scholar.google.com/scholar?q={record.title}"
        )
        data["publication"] = record.publication or "Academic Publication"
        data["research_results"] = (
            record.research_results or "Findings and methodology detailed in publication."
        )
        data["error"] = error
        year_str = f" ({record.year})" if record.year else ""
        data["formatted"] = (
            f"### {idx}. {record.title}\n"
            f"- **Authors**: {data['authors_str']}\n"
            f"- **Publication**: {data['publication']}{year_str}\n"
            f"- **DOI / Link**: {data['url']}\n"
            f"- **Research Results & Key Findings**: {data['research_results']}"
        )
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
            d = _as_dict(cached, idx=1)
            docs.append(d)
            paper_keys.append(cached.dedup_key())
            duration_ms = (time.monotonic() - t0) * 1000
            qid = cache.log_query(query, cleaned, ["local"], 1, True, duration_ms)
            cache.link_query_papers(qid, paper_keys)
            return {
                "count": 1,
                "records": docs,
                "formatted_summary": d["formatted"],
                "from_local": True,
                "errors": [],
                "sources_queried": ["local"],
            }

    # 1) Try local FTS pass first (cache-first contract) if not force_refresh
    if not force_refresh:
        hits = cache.search(local_adapt(cleaned), limit=limit, adapted=True)
        for i, r in enumerate(hits, start=1):
            docs.append(_as_dict(r, idx=i))
            paper_keys.append(r.dedup_key())
        if docs:
            logger.info("search served %d local hits from FTS", len(docs))
            duration_ms = (time.monotonic() - t0) * 1000
            qid = cache.log_query(query, cleaned, ["local"], len(docs), True, duration_ms)
            cache.link_query_papers(qid, paper_keys)
            summary = "\n\n---\n\n".join(d["formatted"] for d in docs)
            return {
                "count": len(docs),
                "records": docs,
                "formatted_summary": summary,
                "from_local": True,
                "errors": [],
                "sources_queried": ["local"],
            }

    if local_only:
        duration_ms = (time.monotonic() - t0) * 1000
        qid = cache.log_query(query, cleaned, ["local"], 0, True, duration_ms)
        return {
            "count": 0,
            "records": [],
            "formatted_summary": "Tidak ada artikel ditemukan di cache lokal.",
            "from_local": True,
            "errors": [],
            "sources_queried": ["local"],
        }

    # 2) Upstream pass on local miss or force_refresh
    upstream = [s for s in requested if s != "local"]
    upstream_query = cleaned
    if ident and (
        (ident["type"] == "doi" and "crossref" in upstream)
        or (ident["type"] == "arxiv" and "arxiv" in upstream)
    ):
        upstream_query = ident["value"]

    records, errors = registry.search_all(upstream_query, sources=upstream, limit=limit)
    cache.upsert_many(records)
    logger.info("search persisted %d upstream records", len(records))
    for i, r in enumerate(records, start=1):
        docs.append(_as_dict(r, idx=i))
        paper_keys.append(r.dedup_key())

    duration_ms = (time.monotonic() - t0) * 1000
    qid = cache.log_query(query, cleaned, upstream, len(docs), False, duration_ms)
    cache.link_query_papers(qid, paper_keys)

    summary = (
        "\n\n---\n\n".join(d["formatted"] for d in docs)
        if docs
        else "Tidak ada artikel ditemukan dari sumber eksternal."
    )
    return {
        "count": len(docs),
        "records": docs,
        "formatted_summary": summary,
        "from_local": False,
        "errors": errors,
        "sources_queried": upstream,
    }


def download_paper(
    cache: PaperCache,
    *,
    doi: str | None = None,
    url: str | None = None,
    pdf_url: str | None = None,
    force_fallback: bool = False,
) -> dict:
    """Download a paper's PDF, extract clean markdown, and persist it to SQLite.

    Automatically resolves Open Journal Systems (OJS) landing pages, DOAJ fulltext URLs,
    Garuda publisher pages, and DOI redirect targets to direct PDF streams and extracts
    structured Markdown.
    """
    target_url = url or pdf_url
    if not doi and not target_url:
        raise ValueError("need at least one of doi, url, or pdf_url")

    # If doi is passed as a full URL, parse it
    if doi and (doi.startswith("http://") or doi.startswith("https://")):
        if "doi.org/10." in doi:
            ident = extract_identifier(doi)
            if ident and ident[0] == "doi":
                doi = ident[1]
        else:
            target_url = target_url or doi
            doi = None

    # Check local cache first
    cached = None
    lookup_key = (doi or target_url or "").lower()
    if lookup_key:
        cached = cache.get_by_key(lookup_key)
    if cached:
        if cached.markdown:
            logger.info("returning cached markdown for %s", lookup_key)
            return {
                "markdown": cached.markdown,
                "pdf_url": cached.pdf_url or target_url or "",
                "doi": cached.doi or doi,
                "title": cached.title,
                "authors": cached.authors,
                "publication": cached.publication,
                "via_unpaywall": False,
                "used_fallback": False,
                "from_cache": True,
            }
        if not target_url:
            target_url = cached.pdf_url or cached.url

    body: bytes | None = None
    final_pdf_url: str | None = None
    resolved_meta: OjsArticleMetadata | None = None
    via_unpaywall: bool = False

    # 1. Direct PDF download attempt (if target_url explicitly points to .pdf)
    if target_url and target_url.lower().endswith(".pdf"):
        try:
            body, _ = download_pdf(target_url)
            final_pdf_url = target_url
        except Exception as e:
            logger.info("direct pdf download failed (%s); trying OJS resolver", e)

    # 2. OJS & Academic Landing Page Resolver
    target_for_resolver = target_url or (f"https://doi.org/{doi}" if doi else None)
    if not body and target_for_resolver:
        try:
            body, resolved_meta = resolve_and_download_ojs(target_for_resolver)
            final_pdf_url = resolved_meta.pdf_url or target_url
            if resolved_meta.doi and not doi:
                doi = resolved_meta.doi
            logger.info("successfully resolved via OJS engine: %s", final_pdf_url)
        except Exception as e:
            logger.info("OJS resolver failed for %s (%s)", target_for_resolver, e)

    # 3. Unpaywall fallback if DOI is available
    if not body and doi:
        try:
            oa_url = resolve_pdf_with_unpaywall(doi)
            if oa_url:
                body, _ = download_pdf(oa_url)
                final_pdf_url = oa_url
                via_unpaywall = True
        except Exception as e:
            logger.info("unpaywall fallback failed (%s)", e)

    if not body:
        raise PdfError(
            f"failed to download paper via direct link, OJS engine, or Unpaywall "
            f"(target: {target_for_resolver})"
        )

    md, used_fallback = extract_markdown(body, force_fallback=force_fallback)

    # Cache enrichment: persist record and markdown into SQLite
    rec = cached or PaperRecord(
        source="ojs" if (resolved_meta and resolved_meta.is_ojs) else "direct",
        source_id=doi or target_url or "unknown",
        title=(
            resolved_meta.title if (resolved_meta and resolved_meta.title) else "Untitled Article"
        ),
        authors=(resolved_meta.authors if (resolved_meta and resolved_meta.authors) else []),
        doi=doi,
        url=target_url or (f"https://doi.org/{doi}" if doi else ""),
        publication=(
            resolved_meta.publication
            if (resolved_meta and resolved_meta.publication)
            else "Open Access Journal"
        ),
        abstract=(resolved_meta.abstract if (resolved_meta and resolved_meta.abstract) else None),
        year=resolved_meta.year if (resolved_meta and resolved_meta.year) else None,
        pdf_url=final_pdf_url,
    )
    if resolved_meta:
        if resolved_meta.title and rec.title in ("Untitled Article", "Unknown Title", ""):
            rec.title = resolved_meta.title
        if resolved_meta.authors and not rec.authors:
            rec.authors = resolved_meta.authors
        if resolved_meta.doi and not rec.doi:
            rec.doi = resolved_meta.doi
        if resolved_meta.publication and rec.publication in (
            "DOAJ Open Access Journal",
            "Garuda Kemdiktisaintek Index",
            "Open Access Journal",
        ):
            rec.publication = resolved_meta.publication
        if resolved_meta.abstract and not rec.abstract:
            rec.abstract = resolved_meta.abstract
        if resolved_meta.year and not rec.year:
            rec.year = resolved_meta.year

    if final_pdf_url:
        rec.pdf_url = final_pdf_url
    rec.markdown = md

    cache.upsert(rec)
    target_key = doi or final_pdf_url or target_url
    if target_key:
        cache.upsert_markdown(target_key, md)

    return {
        "markdown": md,
        "pdf_url": final_pdf_url,
        "doi": rec.doi,
        "title": rec.title,
        "authors": rec.authors,
        "publication": rec.publication,
        "via_unpaywall": via_unpaywall,
        "used_fallback": used_fallback,
        "is_ojs": bool(resolved_meta and resolved_meta.is_ojs),
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
