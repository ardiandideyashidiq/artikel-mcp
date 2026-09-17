"""PubMed Central (NCBI E-utilities) adapter."""

from __future__ import annotations

import json
import logging
import re

from artikel_mcp.http import HttpClient, HttpError, get_client
from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter, clean_html

logger = logging.getLogger("artikel_mcp.sources.pmc")

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class PmcAdapter(SourceAdapter):
    name = "pmc"

    def __init__(self, client: HttpClient | None = None):
        self._client = client or get_client()

    def search(self, query: str, limit: int = 10) -> list[PaperRecord]:
        try:
            ids_raw = self._client.get(
                ESEARCH,
                params={
                    "db": "pmc",
                    "term": query,
                    "retmax": str(limit),
                    "retmode": "json",
                },
            )
            ids = json.loads(ids_raw.decode("utf-8"))["esearchresult"]["idlist"]
        except (HttpError, ValueError, KeyError) as e:
            if isinstance(e, HttpError):
                raise AdapterError(f"pmc esearch failed: {e}") from e
            raise AdapterError(f"pmc esearch unparseable: {e}") from e

        if not ids:
            return []

        try:
            sum_raw = self._client.get(
                ESUMMARY,
                params={
                    "db": "pmc",
                    "id": ",".join(ids),
                    "retmode": "json",
                },
            )
            result = json.loads(sum_raw.decode("utf-8"))["result"]
        except (HttpError, ValueError, KeyError) as e:
            if isinstance(e, HttpError):
                raise AdapterError(f"pmc esummary failed: {e}") from e
            raise AdapterError(f"pmc esummary unparseable: {e}") from e

        records: list[PaperRecord] = []
        for uid in ids:
            entry = result.get(uid)
            if not entry:
                continue
            try:
                records.append(self._map_entry(uid, entry))
            except Exception as e:
                logger.warning("pmc mapping skipped record: %s", e)
        return records

    def _map_entry(self, uid: str, entry: dict) -> PaperRecord:
        title = entry.get("title")
        if not title:
            raise AdapterError("missing title")
        article_ids = {a["idtype"]: a["value"] for a in entry.get("articleids", [])}
        pmcid = article_ids.get("pmcid")
        doi = _extract_doi(article_ids, entry)
        pdf = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/" if pmcid else None
        source_id = pmcid if pmcid else uid
        publication = entry.get("fulljournalname") or entry.get("source") or "PubMed Central"
        url = (
            f"https://doi.org/{doi}"
            if doi
            else f"https://www.ncbi.nlm.nih.gov/pmc/articles/{source_id}/"
        )
        return PaperRecord(
            source=self.name,
            source_id=source_id,
            title=clean_html(title) or title,
            authors=[a["name"] for a in entry.get("authors", [])],
            doi=doi,
            url=url,
            publication=publication,
            abstract=f"Artikel terindeks di PubMed Central: {publication}.",
            year=(
                int(re.search(r"\b(19\d\d|20\d\d)\b", str(entry.get("pubdate") or "")).group(1))
                if re.search(r"\b(19\d\d|20\d\d)\b", str(entry.get("pubdate") or ""))
                else None
            ),
            pdf_url=pdf,
            extra={"pmid": article_ids.get("pmid"), "journal": publication},
        )

    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        recs = self.search(query)
        logger.info("pmc smoke: %d records", len(recs))
        return recs


def _extract_doi(article_ids: dict, entry: dict) -> str | None:
    if "doi" in article_ids and article_ids["doi"]:
        return str(article_ids["doi"]).strip()
    if entry.get("elocationid", "").startswith("10."):
        return entry["elocationid"]
    pii = article_ids.get("pii")
    if pii and pii.startswith("10."):
        return pii
    return None
