"""arXiv Atom API adapter."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter

logger = logging.getLogger("artikel_mcp.sources.arxiv")

BASE = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


class ArxivAdapter(SourceAdapter):
    name = "arxiv"

    def __init__(self, client: HttpClient | None = None):
        self._client = client or get_client()

    def search(self, query: str, limit: int = 20) -> list[PaperRecord]:
        try:
            raw = self._client.get(
                BASE, params={"search_query": f"all:{query}", "max_results": str(limit)}
            )
        except HttpError as e:
            raise AdapterError(f"arxiv request failed: {e}") from e
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise AdapterError(f"arxiv response unparseable: {e}") from e

        records: list[PaperRecord] = []
        for entry in root.findall(f"{ATOM}entry"):
            try:
                records.append(self._map_entry(entry))
            except Exception as e:
                logger.warning("arxiv mapping skipped record: %s", e)
        return records

    def _map_entry(self, entry) -> PaperRecord:
        eid = entry.find(f"{ATOM}id")
        title_el = entry.find(f"{ATOM}title")
        if eid is None or title_el is None:
            raise AdapterError("arity entry missing id or title")
        source_id = eid.text.rsplit("/", 1)[-1]
        authors = [
            a.findtext(f"{ATOM}name")
            for a in entry.findall(f"{ATOM}author")
            if a.findtext(f"{ATOM}name")
        ]
        pdf = None
        for link in entry.findall(f"{ATOM}link"):
            if link.get("title") == "pdf":
                pdf = link.get("href")
                break
        doi = entry.findtext(f"{ARXIV}doi")
        published = entry.findtext(f"{ATOM}published")
        year = int(published[:4]) if published else None
        return PaperRecord(
            source=self.name,
            source_id=source_id,
            title=re_space(title_el.text),
            authors=authors,
            doi=doi,
            abstract=re_space(entry.findtext(f"{ATOM}summary")),
            year=year,
            pdf_url=pdf,
            extra={"entry_id": eid.text},
        )

    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        recs = self.search(query)
        logger.info("arxiv smoke: %d records", len(recs))
        return recs


def re_space(text: str | None) -> str | None:
    if not text:
        return None
    return " ".join(str(text).split())