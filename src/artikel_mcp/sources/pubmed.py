"""PubMed NCBI Entrez API adapter.

Provides direct access to PubMed biomedical citations and abstracts
via NCBI E-utilities (esearch and esummary).
"""

from __future__ import annotations

import json
import logging
import re

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter, clean_html

logger = logging.getLogger("artikel_mcp.sources.pubmed")

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class PubmedAdapter(SourceAdapter):
    name = "pubmed"

    def __init__(self, client: HttpClient | None = None):
        self._client = client or get_client()

    def search(self, query: str, limit: int = 10) -> list[PaperRecord]:
        # Step 1: E-search for PMIDs
        try:
            raw_search = self._client.get(
                ESEARCH_URL,
                params={
                    "db": "pubmed",
                    "term": query,
                    "retmode": "json",
                    "retmax": str(min(max(1, limit), 50)),
                    "sort": "relevance",
                },
            )
        except HttpError as e:
            raise AdapterError(f"pubmed search request failed: {e}") from e

        try:
            search_data = json.loads(raw_search.decode("utf-8"))
            id_list: list[str] = search_data.get("esearchresult", {}).get("idlist", [])
        except (ValueError, KeyError, TypeError) as e:
            raise AdapterError(f"pubmed search unparseable: {e}") from e

        if not id_list:
            return []

        # Step 2: E-summary for metadata
        try:
            raw_summary = self._client.get(
                ESUMMARY_URL,
                params={
                    "db": "pubmed",
                    "id": ",".join(id_list),
                    "retmode": "json",
                },
            )
        except HttpError as e:
            raise AdapterError(f"pubmed summary request failed: {e}") from e

        try:
            summary_data = json.loads(raw_summary.decode("utf-8"))
            result_dict = summary_data.get("result", {})
            uids = result_dict.get("uids", id_list)
        except (ValueError, KeyError, TypeError) as e:
            raise AdapterError(f"pubmed summary unparseable: {e}") from e

        records: list[PaperRecord] = []
        for uid in uids:
            it = result_dict.get(str(uid))
            if not isinstance(it, dict):
                continue
            try:
                rec = self._map_item(str(uid), it)
                if rec:
                    records.append(rec)
            except Exception as e:
                logger.warning("pubmed mapping skipped record %s: %s", uid, e)

        return records

    def _map_item(self, pmid: str, it: dict) -> PaperRecord | None:
        title = it.get("title")
        if not title:
            return None

        # Authors
        authors: list[str] = []
        for a in it.get("authors") or []:
            name = a.get("name")
            if name and isinstance(name, str):
                authors.append(name.strip())

        # Year from pubdate
        pubdate = str(it.get("pubdate") or "")
        year = None
        match = re.search(r"\b(19\d\d|20\d\d)\b", pubdate)
        if match:
            year = int(match.group(1))

        # DOI & PMC ID from articleids
        doi = None
        pmc_id = None
        for aid in it.get("articleids") or []:
            idtype = aid.get("idtype")
            val = aid.get("value")
            if idtype == "doi" and val:
                doi = str(val).strip()
            elif idtype == "pmc" and val:
                pmc_id = str(val).strip()

        publication = it.get("source") or "PubMed"
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        if doi:
            url = f"https://doi.org/{doi}"

        pdf_url = None
        if pmc_id:
            # PMC open access direct link candidate
            pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/pdf/"

        return PaperRecord(
            source=self.name,
            source_id=pmid,
            title=clean_html(title) or title,
            authors=authors,
            doi=doi,
            url=url,
            publication=publication,
            abstract=None,  # esummary does not return full abstracts
            year=year,
            pdf_url=pdf_url,
            extra={"pmid": pmid, "pmc": pmc_id, "pubdate": pubdate},
        )

    def smoke(self, query: str = "crispr gene editing") -> list[PaperRecord]:
        recs = self.search(query, limit=5)
        logger.info("pubmed smoke: %d records", len(recs))
        return recs
