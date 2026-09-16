"""Crossref REST API adapter."""

from __future__ import annotations

import json
import logging

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter, clean_html

logger = logging.getLogger("artikel_mcp.sources.crossref")

BASE = "https://api.crossref.org/works"


class CrossrefAdapter(SourceAdapter):
    name = "crossref"

    def __init__(self, client: HttpClient | None = None):
        self._client = client or get_client()

    def search(self, query: str, limit: int = 20) -> list[PaperRecord]:
        try:
            raw = self._client.get(
                BASE,
                params={
                    "query": query,
                    "rows": str(limit),
                    "select": "DOI,title,author,abstract,issued,link,URL,container-title,publisher",
                },
            )
        except HttpError as e:
            raise AdapterError(f"crossref request failed: {e}") from e
        try:
            data = json.loads(raw.decode("utf-8"))
            items = data["message"]["items"]
        except (ValueError, KeyError, TypeError) as e:
            raise AdapterError(f"crossref response unparseable: {e}") from e

        records: list[PaperRecord] = []
        for it in items:
            try:
                records.append(self._map_item(it))
            except Exception as e:  # single bad record must not abort the rest
                logger.warning("crossref mapping skipped record: %s", e)
        return records

    def _map_item(self, it: dict) -> PaperRecord:
        title = (it.get("title") or [None])[0]
        if not title:
            raise AdapterError("missing title")
        authors = []
        for a in it.get("author") or []:
            name = " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
            if name:
                authors.append(name)
        issued = it.get("issued", {}).get("date-parts") or [[None]]
        year = issued[0][0]
        link = None
        for ln in it.get("link") or []:
            if ln.get("content-type") == "application/pdf":
                link = ln.get("URL")
                break
        doi = it.get("DOI")
        container = (it.get("container-title") or [None])[0]
        publication = container or it.get("publisher") or "Crossref Publication"
        url = it.get("URL") or (f"https://doi.org/{doi}" if doi else None)
        return PaperRecord(
            source=self.name,
            source_id=doi or title,
            title=clean_html(title) or title,
            authors=authors,
            doi=doi,
            url=url,
            publication=publication,
            abstract=clean_html(it.get("abstract")),
            year=year,
            pdf_url=link,
            extra={"url": it.get("URL"), "journal": publication},
        )

    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        recs = self.search(query)
        logger.info("crossref smoke: %d records", len(recs))
        return recs
