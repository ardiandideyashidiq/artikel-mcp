"""Open Journal Systems (OJS) & academic landing page metadata and PDF resolver."""

from __future__ import annotations

import contextlib
import html
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.pdf import PDF_MAGIC, PdfError
from artikel_mcp.sources.base import clean_html

logger = logging.getLogger("artikel_mcp.ojs")

_META_NAME_CONTENT = re.compile(
    r"""<meta\s+[^>]*?(?:name|property)\s*=\s*["']([^"']+)["'][^>]*?content\s*=\s*["']([^"']*)["']""",
    re.I,
)
_META_CONTENT_NAME = re.compile(
    r"""<meta\s+[^>]*?content\s*=\s*["']([^"']*)["'][^>]*?(?:name|property)\s*=\s*["']([^"']+)["']""",
    re.I,
)
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.DOTALL)
_ANCHOR_TAG = re.compile(
    r"""<a\s+[^>]*?href\s*=\s*["']([^"']+)["'][^>]*?>(.*?)</a>""",
    re.I | re.DOTALL,
)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
_YEAR_RE = re.compile(r"\b(19\d\d|20\d\d)\b")
_PDF_ANCHOR_TEXT = re.compile(r"\b(pdf|download pdf|unduh pdf|unduh|full text)\b", re.I)
_OJS_VIEW_RE = re.compile(r"(/article/view/\d+)/(\d+)")
_DOAJ_ARTICLE_RE = re.compile(r"doaj\.org/article/([a-f0-9]+)", re.I)


@dataclass
class OjsArticleMetadata:
    """Structured academic metadata extracted from OJS / publisher HTML."""

    pdf_url: str | None = None
    doi: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    publication: str | None = None
    abstract: str | None = None
    year: int | None = None
    landing_url: str | None = None
    is_ojs: bool = False


def extract_ojs_metadata(html_content: str, base_url: str) -> OjsArticleMetadata:
    """Extract metadata and PDF download locations from OJS or academic HTML."""
    meta_tags: dict[str, list[str]] = {}

    def _add_meta(k: str, v: str) -> None:
        key = k.strip().lower()
        val = html.unescape(v.strip())
        if val:
            meta_tags.setdefault(key, []).append(val)

    for m in _META_NAME_CONTENT.finditer(html_content):
        _add_meta(m.group(1), m.group(2))
    for m in _META_CONTENT_NAME.finditer(html_content):
        _add_meta(m.group(2), m.group(1))

    is_ojs = any(
        "open journal systems" in gen.lower() for gen in meta_tags.get("generator", [])
    ) or ("/article/view/" in base_url or "/article/download/" in base_url)

    # 1. PDF URL
    pdf_url: str | None = None
    if "citation_pdf_url" in meta_tags:
        pdf_url = meta_tags["citation_pdf_url"][0]

    # 2. DOI
    doi: str | None = None
    for doi_key in ["citation_doi", "dc.identifier.doi", "dc.identifier"]:
        for cand in meta_tags.get(doi_key, []):
            m = _DOI_RE.search(cand)
            if m:
                doi = m.group(1).rstrip(".")
                break
        if doi:
            break

    # 3. Title
    title: str | None = None
    for t_key in ["citation_title", "dc.title"]:
        if t_key in meta_tags:
            title = clean_html(meta_tags[t_key][0])
            break
    if not title:
        m_title = _TITLE_TAG.search(html_content)
        if m_title:
            raw_title = clean_html(m_title.group(1)) or ""
            # Strip journal name suffixes like " | Indonesian Comparative Law Review"
            title = re.split(r"\s+[|—–-]\s+", raw_title)[0].strip() or raw_title

    # 4. Authors
    authors: list[str] = []
    for a_key in ["citation_author", "dc.creator.personalname", "dc.creator"]:
        for name in meta_tags.get(a_key, []):
            c_name = clean_html(name)
            if c_name and c_name not in authors:
                authors.append(c_name)

    # 5. Publication / Journal
    publication: str | None = None
    for p_key in ["citation_journal_title", "dc.source"]:
        if p_key in meta_tags:
            publication = clean_html(meta_tags[p_key][0])
            break

    # 6. Abstract
    abstract: str | None = None
    for ab_key in ["citation_abstract", "dc.description"]:
        if ab_key in meta_tags:
            abstract = clean_html(meta_tags[ab_key][0])
            break

    # 7. Year
    year: int | None = None
    for y_key in ["citation_publication_date", "citation_date", "dc.date.issued", "dc.date"]:
        if y_key in meta_tags:
            m_yr = _YEAR_RE.search(meta_tags[y_key][0])
            if m_yr:
                year = int(m_yr.group(1))
                break

    # 8. Anchor scanning if no citation_pdf_url found
    if not pdf_url:
        galley_candidates: list[str] = []
        anchor_candidates: list[str] = []
        for m in _ANCHOR_TAG.finditer(html_content):
            href = m.group(1).strip()
            anchor_inner = m.group(2)
            clean_anchor = clean_html(anchor_inner) or ""
            full_tag = m.group(0).lower()
            if (
                "obj_galley_link" in full_tag
                or "galley-link" in full_tag
                or "/article/download/" in href
                or "/article/viewfile/" in href.lower()
            ):
                galley_candidates.append(href)
            elif _PDF_ANCHOR_TEXT.search(clean_anchor) or href.lower().endswith(".pdf"):
                anchor_candidates.append(href)

        chosen = galley_candidates or anchor_candidates
        if chosen:
            pdf_url = chosen[0]

    # Normalize relative URL
    if pdf_url:
        pdf_url = urljoin(base_url, pdf_url)

    return OjsArticleMetadata(
        pdf_url=pdf_url,
        doi=doi,
        title=title,
        authors=authors,
        publication=publication,
        abstract=abstract,
        year=year,
        landing_url=base_url,
        is_ojs=is_ojs,
    )


def build_candidate_pdf_urls(raw_pdf_url: str) -> list[str]:
    """Generate ordered candidate URLs for OJS galley endpoints."""
    candidates = [raw_pdf_url]
    # In OJS 3: /article/view/{article_id}/{galley_id} is viewer;
    # /article/download/{article_id}/{galley_id} is direct PDF stream.
    if "/article/view/" in raw_pdf_url:
        converted = _OJS_VIEW_RE.sub(r"/article/download/\1/\2", raw_pdf_url)
        converted = converted.replace("/article/download//article/view/", "/article/download/")
        if converted != raw_pdf_url:
            candidates.insert(0, converted)
    return candidates


def resolve_and_download_ojs(
    target: str,
    client: HttpClient | None = None,
) -> tuple[bytes, OjsArticleMetadata]:
    """Resolve an OJS article page, DOI landing page, or direct PDF to PDF bytes and metadata.

    Returns (pdf_bytes, OjsArticleMetadata). Raises PdfError if PDF cannot be retrieved.
    """
    client = client or get_client()
    target_clean = target.strip()

    # Determine target landing URL
    if target_clean.startswith("10.") or (
        target_clean.startswith("http") and "doi.org/10." in target_clean
    ):
        m_doi = _DOI_RE.search(target_clean)
        clean_doi = m_doi.group(1) if m_doi else target_clean
        landing_url = f"https://doi.org/{clean_doi}"
    else:
        landing_url = target_clean

    # DOAJ article landing page: DOAJ web pages are behind Cloudflare Turnstile,
    # but the DOAJ REST API (/api/v3/articles/{id}) is open and provides direct access
    # to the DOI, authors, and publisher's fulltext/OJS URL.
    m_doaj = _DOAJ_ARTICLE_RE.search(target_clean)
    if m_doaj:
        doaj_id = m_doaj.group(1)
        doaj_api = f"https://doaj.org/api/v3/articles/{doaj_id}"
        try:
            import json

            raw_doaj = client.get(doaj_api)
            data = json.loads(raw_doaj.decode("utf-8"))
            bib = data.get("bibjson", {})
            doi_found = bib.get("doi")
            if not doi_found:
                for ident in bib.get("identifier") or []:
                    if (ident.get("type") or "").lower() == "doi" and ident.get("id"):
                        doi_found = ident["id"].strip()
                        break
            fulltext_url = None
            candidate_pdf = None
            for link in bib.get("link") or []:
                l_url = link.get("url") or ""
                l_ctype = (link.get("content_type") or "").lower()
                if "pdf" in l_ctype or l_url.lower().endswith(".pdf"):
                    candidate_pdf = l_url
                elif link.get("type") == "fulltext" and not fulltext_url:
                    fulltext_url = l_url

            doi_target = f"https://doi.org/{doi_found}" if doi_found else None
            next_target = candidate_pdf or fulltext_url or doi_target
            if next_target:
                logger.info("resolved DOAJ article %s to target %s", doaj_id, next_target)
                body, meta = resolve_and_download_ojs(next_target, client=client)
                if not meta.doi and doi_found:
                    meta.doi = doi_found
                if not meta.title and bib.get("title"):
                    meta.title = clean_html(bib.get("title"))
                if not meta.authors:
                    meta.authors = [a.get("name") for a in bib.get("author") or [] if a.get("name")]
                if not meta.publication and bib.get("journal", {}).get("title"):
                    meta.publication = clean_html(bib["journal"]["title"])
                if not meta.year and bib.get("year"):
                    with contextlib.suppress(ValueError, TypeError):
                        meta.year = int(bib["year"])
                if not meta.abstract and bib.get("abstract"):
                    meta.abstract = clean_html(bib.get("abstract"))
                return body, meta
        except Exception as e:
            logger.warning("DOAJ API resolution failed for %s (%s)", doaj_id, e)

    logger.info("resolving landing/PDF URL: %s", landing_url)
    try:
        resp = client.get_response(landing_url)
    except HttpError as e:
        # If doi.org 404'd, try Crossref works API for direct publisher URL
        if "doi.org" in landing_url:
            m_doi = _DOI_RE.search(landing_url)
            if m_doi:
                cr_url = f"https://api.crossref.org/works/{m_doi.group(1)}"
                try:
                    import json

                    cr_raw = client.get(cr_url)
                    cr_data = json.loads(cr_raw.decode("utf-8")).get("message", {})
                    pub_url = cr_data.get("URL") or (
                        cr_data.get("resource", {}).get("primary", {}).get("URL")
                    )
                    if pub_url:
                        logger.info("resolved DOI via Crossref API to %s", pub_url)
                        resp = client.get_response(pub_url)
                        landing_url = resp.url
                    else:
                        raise PdfError(f"CrossRef has no resource URL for DOI {m_doi.group(1)}")
                except Exception as inner_e:
                    raise PdfError(
                        f"failed to resolve DOI {landing_url}: {e} (Crossref fallback: {inner_e})"
                    ) from inner_e
        else:
            raise PdfError(f"failed to fetch {landing_url}: {e}") from e

    # Case 1: Direct PDF stream
    content_type = resp.headers.get("content-type", "").lower()
    if resp.content.startswith(PDF_MAGIC) or "application/pdf" in content_type:
        meta = OjsArticleMetadata(
            pdf_url=resp.url or landing_url,
            landing_url=resp.url or landing_url,
        )
        if "/article/download/" in landing_url:
            view_url = re.sub(
                r"/article/download/(\d+)(?:/\d+)?.*", r"/article/view/\1", landing_url
            )
            if view_url != landing_url:
                try:
                    view_resp = client.get_response(view_url)
                    if "text/html" in view_resp.headers.get("content-type", "").lower():
                        vmeta = extract_ojs_metadata(
                            view_resp.text, base_url=view_resp.url or view_url
                        )
                        vmeta.pdf_url = resp.url or landing_url
                        return resp.content, vmeta
                except Exception as e:
                    logger.debug("failed to enrich metadata from OJS view URL %s: %s", view_url, e)
        return resp.content, meta

    # Case 2: HTML landing page (OJS or publisher page)
    html_text = resp.text
    final_landing_url = resp.url or landing_url
    meta = extract_ojs_metadata(html_text, base_url=final_landing_url)

    if not meta.pdf_url:
        raise PdfError(f"no PDF download link found on page {final_landing_url}")

    candidates = build_candidate_pdf_urls(meta.pdf_url)
    last_err: Exception | None = None
    for cand_url in candidates:
        logger.info("attempting PDF download candidate: %s", cand_url)
        try:
            cand_resp = client.get_response(cand_url)
            if cand_resp.content.startswith(PDF_MAGIC):
                meta.pdf_url = cand_url
                return cand_resp.content, meta

            # If OJS viewer page returned HTML, inspect viewer for iframe/download link
            if "text/html" in cand_resp.headers.get("content-type", "").lower():
                viewer_html = cand_resp.text
                for m_link in _ANCHOR_TAG.finditer(viewer_html):
                    href = m_link.group(1).strip()
                    if "/article/download/" in href or "download" in m_link.group(0).lower():
                        nested_url = urljoin(cand_url, href)
                        logger.info("following nested viewer download URL: %s", nested_url)
                        nested_resp = client.get_response(nested_url)
                        if nested_resp.content.startswith(PDF_MAGIC):
                            meta.pdf_url = nested_url
                            return nested_resp.content, meta
        except Exception as e:
            logger.warning("candidate %s failed: %s", cand_url, e)
            last_err = e

    raise PdfError(
        f"failed to obtain valid PDF from candidate URLs {candidates} "
        f"on {final_landing_url} (last error: {last_err})"
    )
