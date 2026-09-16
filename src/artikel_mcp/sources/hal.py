"""HAL (archives-ouvertes) Solr API adapter."""

from __future__ import annotations

import json
import logging

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter, clean_html

logger = logging.getLogger("artikel_mcp.sources.hal")

BASE = "https://api.archives-ouvertes.fr/search/"
FIELDS = (
    "halId_s,title_s,abstract_s,authFullName_s,doiId_s,producedDateY_i,fileMain_s,uri_s,"
    "journalTitle_s,docType_s"
)


def _first(value) -> str | None:
    if isinstance(value, list):
        return value[0] if value else None
    return value


class HalAdapter(SourceAdapter):
    name = "hal"

    def __init__(self, client: HttpClient | None = None):
        self._client = client or get_client()

    def search(self, query: str, limit: int = 20) -> list[PaperRecord]:
        try:
            raw = self._client.get(
                BASE,
                params={
                    "q": query,
                    "rows": str(limit),
                    "wt": "json",
                    "fl": FIELDS,
                },
            )
        except HttpError as e:
            raise AdapterError(f"hal request failed: {e}") from e
        try:
            data = json.loads(raw.decode("utf-8"))
            docs = data["response"]["docs"]
        except (ValueError, KeyError, TypeError) as e:
            raise AdapterError(f"hal response unparseable: {e}") from e

        records: list[PaperRecord] = []
        for doc in docs:
            try:
                records.append(self._map_doc(doc))
            except Exception as e:
                logger.warning("hal mapping skipped record: %s", e)
        return records

    def _map_doc(self, doc: dict) -> PaperRecord:
        title = _first(doc.get("title_s"))
        if not title:
            raise AdapterError("missing title")
        hal_id = doc.get("halId_s")
        source_id = hal_id or doc.get("uri_s") or title
        doi = _first(doc.get("doiId_s"))
        publication = (
            _first(doc.get("journalTitle_s")) or _first(doc.get("docType_s")) or "HAL Open Science"
        )
        url = _first(doc.get("uri_s")) or (
            f"https://doi.org/{doi}" if doi else f"https://hal.science/{source_id}"
        )
        return PaperRecord(
            source=self.name,
            source_id=source_id,
            title=clean_html(title) or title,
            authors=[a for a in (doc.get("authFullName_s") or []) if a],
            doi=doi,
            url=url,
            publication=publication,
            abstract=clean_html(_first(doc.get("abstract_s"))),
            year=doc.get("producedDateY_i"),
            pdf_url=_first(doc.get("fileMain_s")),
            extra={"hal_id": hal_id, "uri": url, "journal": publication},
        )

    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        recs = self.search(query)
        logger.info("hal smoke: %d records", len(recs))
        return recs
