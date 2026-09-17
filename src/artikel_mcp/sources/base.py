"""Base types for source adapters."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from artikel_mcp.models import PaperRecord


def clean_html(raw: str | None) -> str | None:
    """Strip common academic HTML (JATS/paragraph tags) to plain text."""
    if not raw:
        return None
    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", text).strip() or None


class AdapterError(RuntimeError):
    """Raised when a source adapter fails (network, parse, or structure)."""


class SourceAdapter(ABC):
    name: str

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list[PaperRecord]:
        """Search this source and return normalized records."""

    @abstractmethod
    def smoke(self, query: str = "deep learning") -> list[PaperRecord]:
        """Live smoke test used in verification."""
