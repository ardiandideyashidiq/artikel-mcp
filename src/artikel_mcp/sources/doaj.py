"""DOAJ API v3 adapter."""

from __future__ import annotations

import json
import logging

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter, clean_html

logger = logging.getLogger("artikel_mcp.sources.doaj")

BASE = "https://doaj.org/api/v3/search/articles/"


class DoajAdapter(SourceAdapter):
    name = "doaj"

    def __init__(self, client: HttpClient | None = None):
        self._client = client or get_client()

    def search(self, query: str, limit: int = 20) -> list[PaperRecord]:
        try:
            raw = self._client.get(BASE + query, params={"pageSize": str(limit)})
        except HttpError as e:
            raise AdapterError(f"doaj request failed: {e}") from e
        try:
            data = json.loads(raw.decode("utf-8"))
            results = data.get("results", [])
        except (ValueError, TypeError) as e:
            raise AdapterError(f"doaj response unparseable: {e}") from e

        records: list[PaperRecord] = []
        for it in results:
            try:
                records.append(self._map_item(it))
            except Exception as e:
                logger.warning("doaj mapping skipped record: %s", e)
        return records

    def _map_item(self, it: dict) -> PaperRecord:
        bi = it.get("bibjson", {})
        title = bi.get("title")
        if not title:
            raise AdapterError("missing title")
        doi = bi.get("doi")
        source_id = doi or it.get("id") or title
        pdf = None
        fulltext = None
        for link in bi.get("link") or []:
            url = link.get("url") or ""
            ctype = link.get("content_type", "") or ""
            if "pdf" in ctype.lower() or url.lower().endswith(".pdf"):
                pdf = pdf or url
            elif link.get("type") == "fulltext" and not fulltext:
                fulltext = url
        authors = [a.get("name") for a in bi.get("author") or [] if a.get("name")]
        year = bi.get("year")
        return PaperRecord(
            source=self.name,
            source_id=source_id,
            title=clean_html(title) or title,
            authors=authors,
            doi=doi,
            abstract=clean_html(bi.get("abstract")),
            year=year,
            pdf_url=pdf,
            extra={
                "journal": bi.get("journal", {}).get("title"),
                "volume": bi.get("journal", {}).get("volume"),
                "fulltext_url": fulltext,
            },
        )

    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        recs = self.search(query)
        logger.info("doaj smoke: %d records", len(recs))
        return recs
