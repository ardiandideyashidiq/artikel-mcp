"""OpenAlex REST API adapter.

OpenAlex is a fully open catalog of 250M+ global scholarly works.
Provides fast multi-field search, author metadata, abstract inverted index,
and direct Open Access PDF links.
"""

from __future__ import annotations

import json
import logging
import os

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter, clean_html

logger = logging.getLogger("artikel_mcp.sources.openalex")

BASE = "https://api.openalex.org/works"


def _reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """Reconstruct plain text from OpenAlex abstract_inverted_index."""
    if not inverted_index or not isinstance(inverted_index, dict):
        return None
    try:
        word_positions: list[tuple[int, str]] = []
        for word, positions in inverted_index.items():
            if isinstance(positions, list):
                for pos in positions:
                    if isinstance(pos, int):
                        word_positions.append((pos, str(word)))
        if not word_positions:
            return None
        word_positions.sort(key=lambda x: x[0])
        return " ".join(word for _, word in word_positions)
    except Exception as e:
        logger.debug("Failed to reconstruct abstract: %s", e)
        return None


class OpenAlexAdapter(SourceAdapter):
    name = "openalex"

    def __init__(self, client: HttpClient | None = None, api_key: str | None = None):
        self._client = client or get_client()
        self._api_key = api_key or os.getenv("OPENALEX_API_KEY")

    def search(self, query: str, limit: int = 10) -> list[PaperRecord]:
        params: dict[str, str | int] = {
            "search": query,
            "per_page": min(max(1, limit), 50),
        }
        if self._api_key:
            params["api_key"] = self._api_key

        headers = {"User-Agent": "artikel-mcp/0.1.0 (mailto:dev@artikel-mcp.org)"}

        try:
            raw = self._client.get(BASE, params=params, headers=headers)
        except HttpError as e:
            if "503" in str(e) or "429" in str(e):
                logger.warning(
                    "openalex search temporarily unavailable (%s). "
                    "Configure OPENALEX_API_KEY for priority access.",
                    e,
                )
                raise AdapterError(f"openalex rate-limited or unavailable: {e}") from e
            raise AdapterError(f"openalex request failed: {e}") from e

        try:
            data = json.loads(raw.decode("utf-8"))
            items = data.get("results", [])
        except (ValueError, KeyError, TypeError) as e:
            raise AdapterError(f"openalex response unparseable: {e}") from e

        records: list[PaperRecord] = []
        for it in items:
            try:
                rec = self._map_item(it)
                if rec:
                    records.append(rec)
            except Exception as e:
                logger.warning("openalex mapping skipped record: %s", e)
        return records

    def _map_item(self, it: dict) -> PaperRecord | None:
        title = it.get("title")
        if not title:
            return None

        # Authors
        authors: list[str] = []
        for authorship in it.get("authorships") or []:
            author = authorship.get("author") or {}
            name = author.get("display_name")
            if name and isinstance(name, str) and name.strip():
                authors.append(name.strip())

        # DOI
        raw_doi = it.get("doi")
        doi = None
        if raw_doi and isinstance(raw_doi, str):
            clean = raw_doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
            doi = clean or None

        # Abstract
        abstract = _reconstruct_abstract(it.get("abstract_inverted_index"))

        # Year
        year = it.get("publication_year")

        # Publication / Venue
        primary_loc = it.get("primary_location") or {}
        source_info = primary_loc.get("source") or {}
        publication = source_info.get("display_name") or "OpenAlex Publication"

        # PDF URL
        pdf_url = primary_loc.get("pdf_url")
        if not pdf_url:
            open_access = it.get("open_access") or {}
            oa_url = open_access.get("oa_url")
            if oa_url and isinstance(oa_url, str):
                low_oa = oa_url.lower()
                if low_oa.endswith(".pdf") or "pdf" in low_oa:
                    pdf_url = oa_url

        # Canonical URL
        url = it.get("doi") or primary_loc.get("landing_page_url") or it.get("id")

        source_id = it.get("id", "").replace("https://openalex.org/", "") or doi or title

        return PaperRecord(
            source=self.name,
            source_id=source_id,
            title=clean_html(title) or title,
            authors=authors,
            doi=doi,
            url=url,
            publication=publication,
            abstract=clean_html(abstract),
            year=year,
            pdf_url=pdf_url,
            extra={
                "openalex_id": it.get("id"),
                "cited_by_count": it.get("cited_by_count", 0),
                "is_oa": (it.get("open_access") or {}).get("is_oa", False),
            },
        )

    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        recs = self.search(query, limit=5)
        logger.info("openalex smoke: %d records", len(recs))
        return recs
