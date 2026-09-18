"""Garuda (Kemdiktisaintek) HTML indexer adapter.

Garuda exposes no JSON API; paper discovery is a server-rendered HTML page.
This adapter scrapes /documents?q= with stable, isolated selectors so a page
restructure fails only this module. Verify selectors against the live page
with the module's smoke() before relying on it.

# ponytail: isolated scraper on selectors tied to current Garuda DOM; page
#           restructure breaks only this source -- reselect when it does.
"""

from __future__ import annotations

import logging
import re

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter

logger = logging.getLogger("artikel_mcp.sources.garuda")

BASE = "https://garuda.kemdiktisaintek.go.id"
DOCS = BASE + "/documents"

_ITEM_START_RE = re.compile(r'<div class="article-item">')
_TITLE_RE = re.compile(
    r'<a class="title-article" href="(/documents/detail/\d+)">\s*<xmp>(.*?)</xmp>',
    re.S,
)
_AUTHOR_RE = re.compile(r'<a class="author-article"[^>]*>\s*<xmp>(.*?)</xmp>', re.S)
_DOWNLOAD_RE = re.compile(r'href="([^"]*/article/download/[^"]+)"')
_SUBTITLE_RE = re.compile(r'<xmp class="subtitle-article">(.*?)</xmp>', re.S)


class GarudaAdapter(SourceAdapter):
    name = "garuda"

    def __init__(self, client: HttpClient | None = None):
        self._client = client or get_client()

    def search(self, query: str, limit: int = 10) -> list[PaperRecord]:
        try:
            html = self._client.get(DOCS, params={"q": query}).decode("utf-8", "replace")
        except HttpError as e:
            raise AdapterError(f"garuda request failed: {e}") from e

        if 'class="article-item"' not in html:
            lower = html.lower()
            empty_indicators = ("tidak ditemukan", "no record", "no result", "empty")
            if any(ind in lower for ind in empty_indicators):
                return []
            raise AdapterError("garuda page structure unrecognized; selectors may be stale")

        records: list[PaperRecord] = []
        start_positions = [m.start() for m in _ITEM_START_RE.finditer(html)]
        for idx, start in enumerate(start_positions):
            end = start_positions[idx + 1] if idx + 1 < len(start_positions) else None
            block = html[start:end]
            title_match = _TITLE_RE.search(block)
            if not title_match:
                logger.warning("garuda item without title; skipping")
                continue
            detail, title = title_match.groups()
            source_id = detail.rsplit("/", 1)[-1]
            authors = [a.strip() for a in _AUTHOR_RE.findall(block) if a.strip()]
            pdf = _DOWNLOAD_RE.search(block)
            sub_match = _SUBTITLE_RE.search(block)
            year = _extract_year(sub_match)
            publication = _extract_publication(sub_match)
            clean_title = " ".join(title.split())
            url = BASE + detail
            if len(records) >= limit:
                break
            records.append(
                PaperRecord(
                    source=self.name,
                    source_id=source_id,
                    title=clean_title,
                    authors=authors,
                    doi=None,
                    url=url,
                    publication=publication,
                    abstract=None,
                    year=year,
                    pdf_url=pdf.group(1) if pdf else None,
                    extra={"detail_url": url, "journal": publication},
                )
            )
        return records

    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        recs = self.search(query)
        logger.info("garuda smoke: %d records", len(recs))
        return recs


def _extract_publication(subtitle_match: re.Match | None) -> str:
    if not subtitle_match:
        return "Garuda Kemdiktisaintek Index"
    raw = " ".join(subtitle_match.group(1).split()).strip()
    return raw or "Garuda Kemdiktisaintek Index"


def _extract_year(subtitle_match: re.Match | None) -> int | None:
    if not subtitle_match:
        return None
    m = re.search(r"\((\d{4})\)", subtitle_match.group(1))
    return int(m.group(1)) if m else None
