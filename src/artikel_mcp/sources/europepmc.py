"""Europe PMC REST API adapter."""

from __future__ import annotations

import json
import logging
import re

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter, clean_html

logger = logging.getLogger("artikel_mcp.sources.europepmc")

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePmcAdapter(SourceAdapter):
    name = "europepmc"

    def __init__(self, client: HttpClient | None = None):
        self._client = client or get_client()

    def search(self, query: str, limit: int = 10) -> list[PaperRecord]:
        try:
            raw = self._client.get(
                BASE,
                params={"query": query, "format": "json", "pageSize": str(limit)},
            )
        except HttpError as e:
            raise AdapterError(f"europepmc request failed: {e}") from e
        try:
            data = json.loads(raw.decode("utf-8"))
            results = data["resultList"]["result"]
        except (ValueError, KeyError, TypeError) as e:
            raise AdapterError(f"europepmc response unparseable: {e}") from e

        records: list[PaperRecord] = []
        for it in results:
            try:
                records.append(self._map_item(it))
            except Exception as e:
                logger.warning("europepmc mapping skipped record: %s", e)
        return records

    def _map_item(self, it: dict) -> PaperRecord:
        title = it.get("title")
        if not title:
            raise AdapterError("missing title")
        source_id = f"{it.get('source', 'MED')}:{it.get('id')}"
        pdf = None
        if it.get("pmcid"):
            clean_pmc = str(it["pmcid"]).strip()
            if clean_pmc.upper().startswith("PMC"):
                clean_pmc = clean_pmc[3:]
            pdf = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{clean_pmc}/pdf/"
        else:
            urls = it.get("fullTextUrlList", {}).get("fullTextUrl", [])
            for u in urls:
                if u.get("documentStyle", "").lower() == "pdf":
                    pdf = u.get("url")
                    break
        authors = []
        if it.get("authorString"):
            authors = [a.strip() for a in it["authorString"].split(",") if a.strip()]
        doi = it.get("doi")
        publication = it.get("journalTitle") or "Europe PMC"
        url = (
            f"https://doi.org/{doi}"
            if doi
            else f"https://europepmc.org/article/{it.get('source', 'MED')}/{it.get('id')}"
        )
        return PaperRecord(
            source=self.name,
            source_id=source_id,
            title=clean_html(title) or title,
            authors=authors,
            doi=doi,
            url=url,
            publication=publication,
            abstract=clean_html(it.get("abstractText")),
            year=(
                int(re.search(r"\b(19\d\d|20\d\d)\b", str(it["pubYear"])).group(1))
                if it.get("pubYear") and re.search(r"\b(19\d\d|20\d\d)\b", str(it["pubYear"]))
                else None
            ),
            pdf_url=pdf,
            extra={"pmcid": it.get("pmcid"), "journal": publication},
        )

    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        recs = self.search(query)
        logger.info("europepmc smoke: %d records", len(recs))
        return recs
