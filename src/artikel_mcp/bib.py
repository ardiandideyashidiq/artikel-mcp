"""BibTeX file ingestion: parse a .bib file into normalized PaperRecords.

Malformed blocks are skipped by the underlying parser (logged); entries that
parse but carry no usable title are skipped with a warning. File-level
failures raise :class:`BibFileError`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from bibtexparser import parse_string

from artikel_mcp.models import PaperRecord
from artikel_mcp.query_broker import extract_identifier

logger = logging.getLogger("artikel_mcp.bib")

_AUTHOR_SPLIT_RE = re.compile(r"\s+and\s+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19\d\d|20\d\d)\b")


class BibFileError(RuntimeError):
    """Raised when the .bib file cannot be read or parsed at all."""


def _field(entry, name: str) -> str | None:
    field_obj = entry.fields_dict.get(name)
    if field_obj is None or field_obj.value is None:
        return None
    value = str(field_obj.value).strip()
    return value or None


def _split_authors(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [a.strip() for a in _AUTHOR_SPLIT_RE.split(raw) if a.strip()]


def _normalize_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    ident = extract_identifier(raw)
    if ident and ident["type"] == "doi":
        return ident["value"]
    return raw


def _normalize_year(raw: str | None) -> int | None:
    if not raw:
        return None
    match = _YEAR_RE.search(raw)
    if not match:
        return None
    return int(match.group(0))


def _map_entry(entry) -> PaperRecord | None:
    title = _field(entry, "title")
    if not title:
        logger.warning("bib entry %r skipped: missing title", getattr(entry, "key", "?"))
        return None

    source_id = (entry.key or "").strip() or title
    journal = _field(entry, "journal")
    publisher = _field(entry, "publisher")
    booktitle = _field(entry, "booktitle")
    publication = journal or booktitle or publisher

    extra: dict = {"entry_type": entry.entry_type}
    for name in ("volume", "number", "pages", "journal", "publisher", "booktitle"):
        value = _field(entry, name)
        if value:
            extra[name] = value

    return PaperRecord(
        source="bib",
        source_id=source_id,
        title=title,
        authors=_split_authors(_field(entry, "author")),
        doi=_normalize_doi(_field(entry, "doi")),
        url=_field(entry, "url"),
        publication=publication,
        abstract=_field(entry, "abstract"),
        year=_normalize_year(_field(entry, "year")),
        extra=extra,
    )


@contextmanager
def collect_parse_errors() -> Iterator[list[str]]:
    """Capture bibtexparser warnings (malformed blocks) as error strings."""

    class _Handler(logging.Handler):
        def __init__(self, sink: list[str]):
            super().__init__(level=logging.WARNING)
            self._sink = sink

        def emit(self, record: logging.LogRecord) -> None:
            self._sink.append(record.getMessage())

    errors: list[str] = []
    bib_logger = logging.getLogger("bibtexparser")
    handler = _Handler(errors)
    previous_level = bib_logger.level
    bib_logger.addHandler(handler)
    bib_logger.setLevel(min(previous_level or logging.WARNING, logging.WARNING))
    try:
        yield errors
    finally:
        bib_logger.removeHandler(handler)
        bib_logger.setLevel(previous_level)


def parse_bib_file(path: str | Path) -> list[PaperRecord]:
    """Parse a .bib file into normalized records.

    Raises BibFileError when the path cannot be read or the file cannot be
    parsed at all. Dirty entries are skipped and logged, never fatal.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise BibFileError(f"cannot read bibliography '{path}': {e}") from e

    try:
        library = parse_string(text)
    except Exception as e:  # bibtexparser raises on hard file-level failures
        raise BibFileError(f"cannot parse bibliography '{path}': {e}") from e

    records: list[PaperRecord] = []
    for entry in library.entries:
        record = _map_entry(entry)
        if record is not None:
            records.append(record)
    logger.info("parsed %d records from %s", len(records), path)
    return records
