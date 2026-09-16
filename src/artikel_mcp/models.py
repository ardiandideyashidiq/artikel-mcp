"""Normalized data model shared across source adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PaperRecord:
    source: str
    source_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    doi: str | None = None
    abstract: str | None = None
    year: int | None = None
    pdf_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Stable identifier: DOI when present, else source:source_id."""
        if self.doi:
            return self.doi.lower()
        return f"{self.source}:{self.source_id}"


def record_to_dict(record: PaperRecord) -> dict[str, Any]:
    data = asdict(record)
    data["metadata"] = data.pop("extra")
    return data
