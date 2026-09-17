"""Application services: search (cache-first), download (PDF + fallback), and history."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict

from artikel_mcp.bib import collect_parse_errors, parse_bib_file
from artikel_mcp.cache import PaperCache
from artikel_mcp.citation import (
    SUPPORTED_STYLES,
    format_bibliography,
    format_in_text,
    format_reference,
)
from artikel_mcp.doc_citation import (
    insert_citation_in_file,
    remove_citation_from_file,
    validate_safe_path,
)
from artikel_mcp.doc_citation import (
    scan_file_citations as doc_scan_citations,
)
from artikel_mcp.doc_citation import (
    sync_file_bibliography as doc_sync_bibliography,
)
from artikel_mcp.export import export_paper
from artikel_mcp.models import PaperRecord
from artikel_mcp.ojs import OjsArticleMetadata, resolve_and_download_ojs
from artikel_mcp.pdf import (
    PdfError,
    download_pdf,
    extract_markdown,
    resolve_pdf_with_openalex,
    resolve_pdf_with_unpaywall,
)
from artikel_mcp.query_broker import (
    extract_identifier,
    extract_query_limit,
    local_adapt,
)
from artikel_mcp.sources import registry

logger = logging.getLogger("artikel_mcp.service")


_STOPWORDS = {
    "tentang",
    "mengenai",
    "terkait",
    "soal",
    "di",
    "ke",
    "dari",
    "pada",
    "dalam",
    "dan",
    "atau",
    "yang",
    "untuk",
    "dengan",
    "ini",
    "itu",
    "adalah",
    "sebagai",
    "artikel",
    "jurnal",
    "paper",
    "penelitian",
    "studi",
    "publikasi",
    "status",
    "the",
    "a",
    "an",
    "and",
    "or",
    "in",
    "on",
    "at",
    "for",
    "to",
    "of",
    "with",
}

_CONTEXT_MODIFIERS = {
    "indonesia",
    "indonesian",
    "hukum",
    "law",
    "legal",
    "pidana",
    "perdata",
    "studi",
    "study",
    "analisis",
    "analysis",
    "jurnal",
    "journal",
    "artikel",
    "article",
    "tinjauan",
    "review",
    "perspektif",
    "perspective",
    "kebijakan",
    "policy",
    "nasional",
    "national",
    "internasional",
    "international",
    "penerapan",
    "implementation",
    "isu",
    "issue",
    "kasus",
    "case",
    "status",
    "tentang",
}


def _extract_meaningful_tokens(query: str) -> list[str]:
    tokens = re.findall(r"\w+", query.lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


def _score_relevance(record: PaperRecord, query_tokens: list[str], raw_query: str) -> float:
    score = 0.0
    title_lower = (record.title or "").lower()
    abstract_lower = (record.abstract or "").lower()
    findings_lower = (record.research_results or "").lower()
    combined_text = f"{title_lower} {abstract_lower} {findings_lower}"

    clean_raw = raw_query.lower().strip()
    if clean_raw and clean_raw in title_lower:
        score += 35.0
    elif clean_raw and clean_raw in combined_text:
        score += 15.0

    matches_in_title = 0
    matches_in_text = 0

    for token in query_tokens:
        if token in title_lower:
            score += 15.0
            matches_in_title += 1
        elif token in combined_text:
            score += 5.0
            matches_in_text += 1

    if query_tokens:
        matched_ratio = (matches_in_title + matches_in_text) / len(query_tokens)
        score += matched_ratio * 10.0
        # If paper matches none of the key query tokens, penalize heavily
        if matches_in_title == 0 and matches_in_text == 0:
            score -= 50.0

    # Core topic sieve: if query has specific domain keywords (e.g. 'deepfake', 'crispr'),
    # candidate papers that match ZERO core keywords are off-topic noise (e.g. marriage law).
    core_tokens = [t for t in query_tokens if t not in _CONTEXT_MODIFIERS]
    if core_tokens:
        core_matches = sum(1 for t in core_tokens if t in combined_text)
        if core_matches == 0:
            score -= 100.0

    # Recency bonus
    if record.year:
        try:
            year_int = int(record.year)
            if year_int >= 2000:
                score += (year_int - 2000) * 0.1
        except (ValueError, TypeError):
            pass

    # PDF bonus
    if record.has_pdf():
        score += 1.0

    return score


def _normalize_title_key(title: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", title.lower())
    return " ".join(cleaned.split())


def _deduplicate_and_rank(
    records: list[PaperRecord],
    query: str,
    limit: int,
    year_min: int | None = None,
    year_max: int | None = None,
) -> list[PaperRecord]:
    tokens = _extract_meaningful_tokens(query)
    seen_doi: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[PaperRecord] = []

    for r in records:
        if r.year is not None:
            try:
                y = int(r.year)
                if year_min is not None and y < year_min:
                    continue
                if year_max is not None and y > year_max:
                    continue
            except (ValueError, TypeError):
                pass
        elif year_min is not None:
            continue

        if r.doi:
            doi_key = r.doi.strip().lower()
            if doi_key in seen_doi:
                continue
            seen_doi.add(doi_key)

        norm_title = _normalize_title_key(r.title or "")
        if norm_title and len(norm_title) > 10:
            if norm_title in seen_titles:
                continue
            seen_titles.add(norm_title)

        unique.append(r)

    scored = [(p, _score_relevance(p, tokens, query)) for p in unique]
    positive_hits = [(p, s) for p, s in scored if s > 0.0]
    candidate_list = positive_hits if positive_hits else scored

    def _sort_key(item: tuple[PaperRecord, float]):
        rec, score = item
        try:
            y = int(rec.year) if rec.year else 0
        except (ValueError, TypeError):
            y = 0
        return (score, y, 1 if rec.has_pdf() else 0)

    candidate_list.sort(key=_sort_key, reverse=True)
    return [item[0] for item in candidate_list[:limit]]


def _record_to_view(record: PaperRecord, idx: int = 1, error: str | None = None) -> dict:
    """Build the 5-part formatted record view shared by search and ingest."""
    data = asdict(record)
    data["dedup_key"] = record.dedup_key()
    data["authors_str"] = ", ".join(record.authors) if record.authors else "Unknown Authors"
    data["url"] = record.url or (
        f"https://doi.org/{record.doi}"
        if record.doi
        else f"https://scholar.google.com/scholar?q={record.title}"
    )
    data["publication"] = record.publication or "Academic Publication"
    findings = record.research_results or record.abstract or "Detailed in publication."
    if len(findings) > 200:
        findings = findings[:197] + "..."
    data["research_results"] = findings
    data["error"] = error
    year_str = f" ({record.year})" if record.year else ""
    data["formatted"] = (
        f"### {idx}. {record.title}\n"
        f"- **Authors**: {data['authors_str']}\n"
        f"- **Publication**: {data['publication']}{year_str}\n"
        f"- **DOI / Link**: {data['url']}\n"
        f"- **Research Results & Key Findings**: {findings}"
    )
    data["has_full_text"] = bool(record.markdown)
    # Compact search list view: truncate long abstracts and findings
    if data.get("abstract") and len(data["abstract"]) > 200:
        data["abstract"] = data["abstract"][:197] + "..."
    # CRITICAL: Strip full markdown text and extra raw metadata from search list view
    data.pop("markdown", None)
    data.pop("extra", None)
    return data


def search_papers(
    cache: PaperCache,
    query: str,
    sources: list[str] | str | None = None,
    source: list[str] | str | None = None,
    limit: int = 10,
    force_refresh: bool = False,
    year_min: int | None = None,
    year_max: int | None = None,
    format_mode: str = "both",
) -> dict:
    """Search academic indexes and local cache with query intelligence and persistence.

    - Defaults strictly to 10 results when no specific count is requested.
    - Strips conversational phrases ('tolong carikan...', 'find papers about...').
    - Detects explicit count requests ('cari 50 artikel tentang...').
    - Detects explicit DOIs and arXiv IDs.
    - Guarantees 5 mandatory fields: title, authors, publication, DOI/link, and research results.
    - Serves cached FTS records when available (unless force_refresh is True).
    - Logs every search query and links results in SQLite for future querying.
    - Supports year_min / year_max filtering and configurable format_mode.
    """
    t0 = time.monotonic()
    cleaned, natural_limit = extract_query_limit(query)
    if natural_limit is not None and limit == 10:
        limit = natural_limit
    ident = extract_identifier(cleaned)

    docs: list[dict] = []
    paper_keys: list[str] = []

    # Normalize source / sources parameter
    raw_sources = sources if sources is not None else source
    if raw_sources is None:
        requested = registry.default_sources()
    elif isinstance(raw_sources, str):
        raw_list = registry.supported_sources() if raw_sources.lower() == "all" else [raw_sources]
        requested = ["local" if s.lower() in ("cache", "db", "database") else s for s in raw_list]
    elif any(isinstance(s, str) and s.lower() == "all" for s in raw_sources):
        requested = registry.supported_sources()
    else:
        requested = [
            "local" if str(s).lower() in ("cache", "db", "database") else s for s in raw_sources
        ]

    local_only = "local" in requested and len(requested) == 1

    # Check identifier fast-path in cache
    if ident and not force_refresh:
        val = ident["value"]
        cached = cache.get_by_key(val)
        if cached:
            d = _record_to_view(cached, idx=1)
            docs.append(d)
            paper_keys.append(cached.dedup_key())
            duration_ms = (time.monotonic() - t0) * 1000
            qid = cache.log_query(query, cleaned, ["local"], 1, True, duration_ms)
            cache.link_query_papers(qid, paper_keys)
            res = {
                "count": 1,
                "from_local": True,
                "errors": [],
                "sources_queried": ["local"],
            }
            if format_mode in ("both", "records"):
                res["records"] = docs
            if format_mode in ("both", "summary"):
                res["formatted_summary"] = d["formatted"]
            return res

    # 1) Try local FTS pass first (cache-first contract) if not force_refresh
    if not force_refresh:
        hits = cache.search(local_adapt(cleaned), limit=limit * 2, adapted=True)
        ranked_hits = _deduplicate_and_rank(
            hits, cleaned, limit=limit, year_min=year_min, year_max=year_max
        )
        for i, r in enumerate(ranked_hits, start=1):
            docs.append(_record_to_view(r, idx=i))
            paper_keys.append(r.dedup_key())
        if docs:
            logger.info("search served %d local hits from FTS", len(docs))
            duration_ms = (time.monotonic() - t0) * 1000
            qid = cache.log_query(query, cleaned, ["local"], len(docs), True, duration_ms)
            cache.link_query_papers(qid, paper_keys)
            summary = "\n\n---\n\n".join(d["formatted"] for d in docs)
            res = {
                "count": len(docs),
                "from_local": True,
                "errors": [],
                "sources_queried": ["local"],
            }
            if format_mode in ("both", "records"):
                res["records"] = docs
            if format_mode in ("both", "summary"):
                res["formatted_summary"] = summary
            return res

    if local_only:
        duration_ms = (time.monotonic() - t0) * 1000
        qid = cache.log_query(query, cleaned, ["local"], 0, True, duration_ms)
        res = {
            "count": 0,
            "from_local": True,
            "errors": [],
            "sources_queried": ["local"],
        }
        if format_mode in ("both", "records"):
            res["records"] = []
        if format_mode in ("both", "summary"):
            res["formatted_summary"] = "Tidak ada artikel ditemukan di cache lokal."
        return res

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

    # Deduplicate across sources, rank by query relevance, and trim to limit
    ranked_records = _deduplicate_and_rank(
        records, cleaned, limit=limit, year_min=year_min, year_max=year_max
    )
    for i, r in enumerate(ranked_records, start=1):
        docs.append(_record_to_view(r, idx=i))
        paper_keys.append(r.dedup_key())

    duration_ms = (time.monotonic() - t0) * 1000
    qid = cache.log_query(query, cleaned, upstream, len(docs), False, duration_ms)
    cache.link_query_papers(qid, paper_keys)

    summary = (
        "\n\n---\n\n".join(d["formatted"] for d in docs)
        if docs
        else "Tidak ada artikel ditemukan dari sumber eksternal."
    )
    res = {
        "count": len(docs),
        "from_local": False,
        "errors": errors,
        "sources_queried": upstream,
    }
    if format_mode in ("both", "records"):
        res["records"] = docs
    if format_mode in ("both", "summary"):
        res["formatted_summary"] = summary
    return res


def download_paper(
    cache: PaperCache,
    *,
    doi: str | None = None,
    url: str | None = None,
    pdf_url: str | None = None,
    force_fallback: bool = False,
    page_start: int = 0,
    page_end: int | None = None,
    max_pages: int = 100,
    summary_only: bool = False,
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
            if ident and ident.get("type") == "doi":
                doi = ident["value"]
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
            out_md = cached.markdown
            is_truncated = False
            if summary_only and len(out_md) > 2000:
                out_md = (
                    out_md[:2000] + "\n\n... [Content truncated in summary mode. "
                    "Set summary_only=False for full text]"
                )
                is_truncated = True
            return {
                "markdown": out_md,
                "pdf_url": cached.pdf_url or target_url or "",
                "doi": cached.doi or doi,
                "title": cached.title,
                "authors": cached.authors,
                "publication": cached.publication,
                "via_unpaywall": False,
                "used_fallback": False,
                "from_cache": True,
                "is_truncated": is_truncated,
                "char_count": len(cached.markdown),
            }
        if not target_url:
            target_url = cached.pdf_url or cached.url

    body: bytes | None = None
    final_pdf_url: str | None = None
    resolved_meta: OjsArticleMetadata | None = None
    via_openalex: bool = False
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

    # 3. OpenAlex Open Access fallback if DOI is available (free, keyless)
    if not body and doi:
        try:
            oa_url = resolve_pdf_with_openalex(doi)
            if oa_url:
                body, _ = download_pdf(oa_url)
                final_pdf_url = oa_url
                via_openalex = True
                logger.info("successfully resolved via OpenAlex OA: %s", final_pdf_url)
        except Exception as e:
            logger.info("openalex fallback failed (%s)", e)

    # 4. Unpaywall fallback if DOI is available
    if not body and doi:
        try:
            oa_url = resolve_pdf_with_unpaywall(doi)
            if oa_url:
                body, _ = download_pdf(oa_url)
                final_pdf_url = oa_url
                via_unpaywall = True
                logger.info("successfully resolved via Unpaywall: %s", final_pdf_url)
        except Exception as e:
            logger.info("unpaywall fallback failed (%s)", e)

    if not body:
        raise PdfError(
            f"failed to download paper via direct link, OJS engine, OpenAlex, or Unpaywall "
            f"(target: {target_for_resolver})"
        )

    try:
        md, used_fallback = extract_markdown(
            body,
            force_fallback=force_fallback,
            page_start=page_start,
            page_end=page_end,
            max_pages=max_pages,
        )
    except TypeError:
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

    key = cache.upsert(rec)
    cache.upsert_markdown(key, md)

    out_md = md
    is_truncated = False
    if summary_only and len(out_md) > 2000:
        out_md = (
            out_md[:2000]
            + "\n\n... [Content truncated in summary mode. Set summary_only=False for full text]"
        )
        is_truncated = True

    return {
        "markdown": out_md,
        "pdf_url": final_pdf_url,
        "doi": rec.doi,
        "title": rec.title,
        "authors": rec.authors,
        "publication": rec.publication,
        "via_openalex": via_openalex,
        "via_unpaywall": via_unpaywall,
        "used_fallback": used_fallback,
        "is_ojs": bool(resolved_meta and resolved_meta.is_ojs),
        "is_truncated": is_truncated,
        "char_count": len(md),
    }


def get_cached_paper(
    cache: PaperCache,
    key_or_doi: str,
    *,
    full_text: bool = True,
    max_chars: int | None = None,
) -> dict | None:
    """Retrieve full metadata and cached markdown for a paper from SQLite."""
    rec = cache.get_by_key(key_or_doi)
    if rec is None:
        return None
    data = asdict(rec)
    data["dedup_key"] = rec.dedup_key()
    if not full_text:
        data.pop("markdown", None)
        data["has_markdown"] = bool(rec.markdown)
    elif max_chars and data.get("markdown") and len(data["markdown"]) > max_chars:
        data["markdown"] = data["markdown"][:max_chars] + (
            f"\n\n... [Content truncated at {max_chars} chars. "
            "Set full_text=True without max_chars for full text]"
        )
        data["is_truncated"] = True
    return data


def get_search_history(cache: PaperCache, query: str | None = None, limit: int = 20) -> list[dict]:
    """Retrieve recent queries and results from SQLite."""
    return cache.get_recent_queries(query=query, limit=limit)


_SCHOLAR_FALLBACK_PREFIX = "https://scholar.google.com/scholar?q="


def ingest_bibliography(
    cache: PaperCache,
    bib_path: str,
    download: bool = True,
    limit: int = 200,
) -> dict:
    """Parse a .bib file, index its entries, and download their full text.

    - Normalizes every entry to the canonical record shape via `bib.parse_bib_file`.
    - Persists all entries to the cache; duplicate DOIs collapse onto one record.
    - With `download` enabled (default), fetches each entry's full text through the
      existing `download_paper` pipeline, serially and isolated per entry.
    - Idempotent: entries already carrying markdown are reported `cached`.
    """
    safe_bib = validate_safe_path(bib_path)
    if not safe_bib.exists():
        raise FileNotFoundError(f"Bibliography file not found: {safe_bib}")

    t0 = time.monotonic()
    with collect_parse_errors() as parse_errors:
        records = parse_bib_file(safe_bib)
    if limit and limit > 0:
        records = records[:limit]

    # Index first so every parsed entry persists even if downloads later fail.
    cache.upsert_many(records)
    logger.info("bib ingest indexed %d records from %s", len(records), bib_path)

    counts = {"downloaded": 0, "cached": 0, "skipped": 0, "failed": 0}
    docs: list[dict] = []
    for idx, rec in enumerate(records, start=1):
        status = "skipped"
        error: str | None = None
        if download:
            existing = cache.get_by_key(rec.dedup_key())
            if existing and existing.markdown:
                rec.markdown = existing.markdown
                status = "cached"
            else:
                has_source = bool(rec.doi) or (
                    rec.url and not rec.url.startswith(_SCHOLAR_FALLBACK_PREFIX)
                )
                if not has_source:
                    error = "no doi or direct url"
                else:
                    try:
                        result = download_paper(cache, doi=rec.doi, url=rec.url)
                        status = "cached" if result.get("from_cache") else "downloaded"
                        if result.get("markdown"):
                            rec.markdown = result["markdown"]
                            cache.upsert(rec)  # attach markdown to this entry's own key
                    except Exception as e:
                        status = "failed"
                        error = str(e)
                        logger.warning("bib download failed for %s: %s", rec.dedup_key(), e)
        counts[status] += 1
        view = _record_to_view(rec, idx=idx, error=error)
        view["status"] = status
        docs.append(view)

    duration_ms = (time.monotonic() - t0) * 1000
    header = (
        f"Ingested {len(docs)} entries from {bib_path}: "
        f"{counts['downloaded']} downloaded, {counts['cached']} cached, "
        f"{counts['skipped']} skipped, {counts['failed']} failed"
        + (f", {len(parse_errors)} parse error(s)" if parse_errors else "")
        + "."
    )
    if docs:
        body = "\n\n---\n\n".join(d["formatted"] for d in docs)
        formatted_summary = f"{header}\n\n{body}"
    else:
        formatted_summary = header

    logger.info("bib ingest finished in %.0fms: %s", duration_ms, counts)
    return {
        "count": len(docs),
        "records": docs,
        "formatted_summary": formatted_summary,
        "errors": list(parse_errors),
        "summary": counts,
        "bib_path": str(bib_path),
    }


def add_paper(
    cache: PaperCache,
    *,
    title: str,
    authors: list[str] | str | None = None,
    doi: str | None = None,
    url: str | None = None,
    publication: str | None = None,
    year: int | None = None,
    abstract: str | None = None,
    research_results: str | None = None,
    markdown: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Manually add a paper record to SQLite cache and FTS5 index."""
    if not title or not title.strip():
        raise ValueError("paper title cannot be empty")

    authors_list: list[str] = []
    if isinstance(authors, str):
        authors_list = [a.strip() for a in re.split(r"[,;]|\band\b", authors) if a.strip()]
    elif isinstance(authors, list):
        authors_list = [str(a).strip() for a in authors if str(a).strip()]

    source_id = (doi or url or title).strip()
    rec = PaperRecord(
        source="manual",
        source_id=source_id,
        title=title.strip(),
        authors=authors_list,
        doi=doi.strip() if doi else None,
        url=url.strip() if url else None,
        publication=publication.strip() if publication else None,
        year=year,
        abstract=abstract.strip() if abstract else None,
        research_results=research_results.strip() if research_results else None,
        markdown=markdown,
        extra=extra or {},
    )
    key = cache.upsert(rec)
    if markdown:
        cache.upsert_markdown(key, markdown)

    view = _record_to_view(rec, idx=1)
    view["action"] = "created"
    logger.info("added paper '%s' (key=%s)", rec.title, key)
    return {
        "success": True,
        "key": key,
        "record": view,
        "formatted": view["formatted"],
    }


def update_paper(
    cache: PaperCache,
    doi_or_key: str,
    *,
    title: str | None = None,
    authors: list[str] | str | None = None,
    doi: str | None = None,
    url: str | None = None,
    publication: str | None = None,
    year: int | None = None,
    abstract: str | None = None,
    research_results: str | None = None,
    markdown: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Update metadata or notes of an existing paper in SQLite cache and FTS5."""
    rec = cache.get_by_key(doi_or_key)
    if not rec:
        raise ValueError(f"paper '{doi_or_key}' not found in cache")

    if title is not None:
        rec.title = title.strip()
    if authors is not None:
        if isinstance(authors, str):
            rec.authors = [a.strip() for a in re.split(r"[,;]|\band\b", authors) if a.strip()]
        elif isinstance(authors, list):
            rec.authors = [str(a).strip() for a in authors if str(a).strip()]
    if doi is not None:
        rec.doi = doi.strip() if doi else None
    if url is not None:
        rec.url = url.strip() if url else None
    if publication is not None:
        rec.publication = publication.strip() if publication else None
    if year is not None:
        rec.year = year
    if abstract is not None:
        rec.abstract = abstract.strip() if abstract else None
    if research_results is not None:
        rec.research_results = research_results.strip() if research_results else None
    if markdown is not None:
        rec.markdown = markdown
    if extra is not None:
        rec.extra.update(extra)

    key = cache.upsert(rec)
    if markdown is not None:
        cache.upsert_markdown(key, markdown)

    view = _record_to_view(rec, idx=1)
    view["action"] = "updated"
    logger.info("updated paper '%s' (key=%s)", rec.title, key)
    return {
        "success": True,
        "key": key,
        "record": view,
        "formatted": view["formatted"],
    }


def delete_paper(cache: PaperCache, doi_or_key: str) -> dict:
    """Delete a paper record from SQLite cache and FTS5 index."""
    deleted = cache.delete(doi_or_key)
    if not deleted:
        return {
            "success": False,
            "key": doi_or_key,
            "message": f"paper '{doi_or_key}' not found in cache",
        }
    logger.info("deleted paper '%s'", doi_or_key)
    return {
        "success": True,
        "key": doi_or_key,
        "message": f"paper '{doi_or_key}' successfully deleted from cache and search index",
    }


def format_paper_citation(
    cache: PaperCache,
    doi_or_key: str,
    *,
    style: str = "apa7",
    narrative: bool = False,
) -> dict:
    """Format citation for a paper in APA 7th, Chicago, IEEE, MLA 9th, Harvard, or BibTeX."""
    rec = cache.get_by_key(doi_or_key)
    if not rec:
        raise ValueError(f"paper '{doi_or_key}' not found in cache")

    reference_entry = format_reference(rec, style=style)
    in_text = format_in_text(rec, style=style, narrative=narrative)

    return {
        "key": doi_or_key,
        "title": rec.title,
        "style": style,
        "reference": reference_entry,
        "in_text": in_text,
        "supported_styles": SUPPORTED_STYLES,
    }


def export_paper_document(
    cache: PaperCache,
    doi_or_key: str,
    *,
    template: str = "academic",
    style: str = "apa7",
    compile_pdf: bool = True,
    output_dir: str | None = None,
    custom_template: str | None = None,
) -> dict:
    """Export a paper into standardized LaTeX source and compile to PDF."""
    rec = cache.get_by_key(doi_or_key)
    if not rec:
        raise ValueError(f"paper '{doi_or_key}' not found in cache")

    return export_paper(
        rec,
        template=template,
        citation_style=style,
        compile_pdf=compile_pdf,
        output_dir=output_dir,
        custom_template=custom_template,
    )


def export_bibliography_file(
    cache: PaperCache,
    *,
    keys: list[str] | None = None,
    format_type: str = "bibtex",
    style: str = "apa7",
    output_path: str | None = None,
) -> dict:
    """Export multiple cached papers to BibTeX or formatted text bibliography."""
    records: list[PaperRecord] = []
    if keys:
        for k in keys:
            r = cache.get_by_key(k)
            if r:
                records.append(r)
    else:
        records = cache.list_all(limit=500)

    if not records:
        return {
            "count": 0,
            "content": "",
            "message": "No papers found to export.",
        }

    formatted = format_bibliography(records, style="bibtex" if format_type == "bibtex" else style)

    if output_path:
        out_p = validate_safe_path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", dir=out_p.parent, delete=False, encoding="utf-8"
        ) as tf:
            tf.write(formatted)
            temp_name = tf.name
        os.replace(temp_name, out_p)
        logger.info("exported %d papers to %s", len(records), out_p)

    return {
        "count": len(records),
        "format": format_type,
        "style": style if format_type != "bibtex" else "bibtex",
        "output_path": str(output_path) if output_path else None,
        "content": formatted,
    }


def scan_document_citations(
    cache: PaperCache,
    file_path: str,
) -> dict:
    """Scan a document file for citations and resolve against the local library."""
    return doc_scan_citations(file_path, cache)


def insert_document_citation(
    cache: PaperCache,
    file_path: str,
    doi_or_key: str,
    *,
    line_number: int | None = None,
    marker_format: str = "pandoc",
    style: str = "apa7",
    narrative: bool = False,
    auto_sync: bool = True,
) -> dict:
    """Insert a citation into a document and optionally sync its bibliography."""
    return insert_citation_in_file(
        file_path,
        doi_or_key,
        cache,
        line_number=line_number,
        marker_format=marker_format,
        style=style,
        narrative=narrative,
        auto_sync=auto_sync,
    )


def remove_document_citation(
    cache: PaperCache,
    file_path: str,
    doi_or_key: str,
    *,
    sync_bib: bool = True,
    style: str = "apa7",
) -> dict:
    """Remove citations of a paper from a document and sync its bibliography."""
    return remove_citation_from_file(
        file_path,
        doi_or_key,
        cache,
        sync_bib=sync_bib,
        style=style,
    )


def sync_document_bibliography(
    cache: PaperCache,
    file_path: str,
    *,
    style: str = "apa7",
    section_heading: str = "## References",
    companion_bib: bool = True,
) -> dict:
    """Scan citations in a document and automatically generate/update its bibliography section."""
    return doc_sync_bibliography(
        file_path,
        cache,
        style=style,
        section_heading=section_heading,
        companion_bib=companion_bib,
    )
