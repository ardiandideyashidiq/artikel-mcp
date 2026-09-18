"""Semantic Scholar REST API adapter.

Provides AI-backed academic search with TLDR summaries and citation counts.
Supports optional SEMANTIC_SCHOLAR_API_KEY or PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY.
"""

from __future__ import annotations

import json
import logging
import os

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter, clean_html

logger = logging.getLogger("artikel_mcp.sources.semantic")

BASE = "https://api.semanticscholar.org/graph/v1/paper/search"


class SemanticAdapter(SourceAdapter):
    name = "semantic"

    def __init__(self, client: HttpClient | None = None, api_key: str | None = None):
        self._client = client or get_client()
        self._api_key = (
            api_key
            or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
            or os.getenv("PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY")
        )

    def search(self, query: str, limit: int = 10) -> list[PaperRecord]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key

        fields = (
            "paperId,title,abstract,authors,year,venue,"
            "openAccessPdf,externalIds,url,tldr,citationCount"
        )
        try:
            raw = self._client.get(
                BASE,
                params={
                    "query": query,
                    "limit": str(min(max(1, limit), 50)),
                    "fields": fields,
                },
                headers=headers or None,
            )
        except HttpError as e:
            if "429" in str(e):
                logger.warning(
                    "semantic scholar rate limited (HTTP 429). "
                    "Configure SEMANTIC_SCHOLAR_API_KEY for higher quotas."
                )
                raise AdapterError(f"semantic scholar rate limited: {e}") from e
            raise AdapterError(f"semantic scholar request failed: {e}") from e

        try:
            data = json.loads(raw.decode("utf-8"))
            items = data.get("data", [])
        except (ValueError, KeyError, TypeError) as e:
            raise AdapterError(f"semantic scholar response unparseable: {e}") from e

        records: list[PaperRecord] = []
        for it in items:
            try:
                rec = self._map_item(it)
                if rec:
                    records.append(rec)
            except Exception as e:
                logger.warning("semantic scholar mapping skipped record: %s", e)
        return records

    def _map_item(self, it: dict) -> PaperRecord | None:
        title = it.get("title")
        if not title:
            return None

        # Authors
        authors: list[str] = []
        for a in it.get("authors") or []:
            name = a.get("name")
            if name and isinstance(name, str):
                authors.append(name.strip())

        # Year
        year = it.get("year")

        # External IDs
        ext_ids = it.get("externalIds") or {}
        doi = ext_ids.get("DOI")

        # Abstract or TLDR fallback
        abstract = it.get("abstract")
        if not abstract:
            tldr = it.get("tldr") or {}
            abstract = tldr.get("text")

        # Publication
        publication = it.get("venue") or "Semantic Scholar"

        # PDF URL
        oa_pdf = it.get("openAccessPdf") or {}
        pdf_url = oa_pdf.get("url")

        # URL
        url = it.get("url") or (f"https://doi.org/{doi}" if doi else None)
        paper_id = it.get("paperId") or doi or title

        return PaperRecord(
            source=self.name,
            source_id=paper_id,
            title=clean_html(title) or title,
            authors=authors,
            doi=doi,
            url=url,
            publication=publication,
            abstract=clean_html(abstract),
            year=year,
            pdf_url=pdf_url,
            extra={
                "paper_id": paper_id,
                "citation_count": it.get("citationCount", 0),
                "arxiv_id": ext_ids.get("ArXiv"),
                "pmid": ext_ids.get("PubMed"),
            },
        )

    def smoke(self, query: str = "machine learning") -> list[PaperRecord]:
        recs = self.search(query, limit=5)
        logger.info("semantic smoke: %d records", len(recs))
        return recs
