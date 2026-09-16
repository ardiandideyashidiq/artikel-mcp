"""Normalized data model shared across source adapters."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_RESULT_KEYWORDS = re.compile(
    r"\b(?:result|results|found|show|shows|showed|demonstrate|demonstrates|"
    r"demonstrated|conclude|concludes|concluded|indicate|indicates|indicated|"
    r"achieve|achieves|achieved|outperform|outperforms|outperformed|observe|"
    r"observed|menunjukkan|hasil|kesimpulan|ditemukan|membuktikan|berhasil|"
    r"signifikan|pengaruh|efektif)\b",
    re.IGNORECASE,
)


def distill_research_results(abstract: str | None, title: str | None = None) -> str:
    """Extract key research findings and conclusions from abstract, or summarize."""
    if not abstract or not abstract.strip():
        if title:
            return (
                f"Fokus penelitian: '{title.strip()}'. "
                "Unduh teks lengkap PDF untuk metodologi dan temuan terperinci."
            )
        return "Informasi hasil penelitian lengkap tersedia dalam dokumen publikasi."

    clean_abs = " ".join(abstract.split())
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_abs) if len(s.strip()) > 15]

    matching = [s for s in sentences if _RESULT_KEYWORDS.search(s)]
    if matching:
        return " ".join(matching[:3])

    if len(sentences) >= 2:
        return " ".join(sentences[-2:])
    elif sentences:
        return sentences[0]

    return clean_abs[:300] + ("..." if len(clean_abs) > 300 else "")


def build_paper_url(record: PaperRecord) -> str:
    """Return a guaranteed clickable, resolvable URL for the paper."""
    if record.url and record.url.strip():
        return record.url.strip()
    if record.doi and record.doi.strip():
        return f"https://doi.org/{record.doi.strip()}"
    if record.source == "arxiv":
        return f"https://arxiv.org/abs/{record.source_id}"
    if record.source == "garuda":
        return f"https://garuda.kemdiktisaintek.go.id/documents/detail/{record.source_id}"
    if record.source == "pmc":
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{record.source_id}/"
    if record.source == "europepmc":
        return f"https://europepmc.org/article/{record.source_id}"
    if record.source == "hal":
        return f"https://hal.science/{record.source_id}"
    if record.source == "doaj":
        return f"https://doaj.org/article/{record.source_id}"
    if record.pdf_url:
        return record.pdf_url
    return f"https://scholar.google.com/scholar?q={record.title}"


@dataclass
class PaperRecord:
    source: str
    source_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    doi: str | None = None
    url: str | None = None
    publication: str | None = None
    abstract: str | None = None
    research_results: str | None = None
    year: int | None = None
    pdf_url: str | None = None
    markdown: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.url:
            self.url = build_paper_url(self)
        if not self.research_results:
            self.research_results = distill_research_results(self.abstract, self.title)
        if not self.publication:
            pub = self.extra.get("journal") or self.extra.get("publisher")
            source_label = (
                f"{self.source.capitalize()} Index" if self.source else "Academic Publication"
            )
            self.publication = pub or source_label

    def dedup_key(self) -> str:
        """Stable identifier: DOI when present, else source:source_id."""
        if self.doi:
            return self.doi.lower()
        return f"{self.source}:{self.source_id}"


def record_to_dict(record: PaperRecord) -> dict[str, Any]:
    data = asdict(record)
    data["metadata"] = data.pop("extra")
    return data
