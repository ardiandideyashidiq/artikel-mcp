"""SQLite + FTS5 persistence layer for fetched paper records."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from artikel_mcp.models import PaperRecord

logger = logging.getLogger("artikel_mcp.cache")

_DB_ENV = "ARTIKEL_MCP_DB"


def default_db_path() -> Path:
    """Platform data dir default (respects XDG on Linux)."""
    env = os.environ.get(_DB_ENV)
    if env:
        return Path(env)
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        home = Path.home()
        if os.name == "nt":
            base = os.environ.get("APPDATA", str(home))
        elif os.name == "posix":
            base = str(home / ".local" / "share")
        else:
            base = str(home)
    return Path(base) / "artikel-mcp" / "papers.db"


@dataclass
class SearchResult:
    records: list[PaperRecord] = field(default_factory=list)
    from_upstream: bool = False
    errors: list[str] = field(default_factory=list)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    dedup_key   TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    doi         TEXT,
    title       TEXT NOT NULL,
    authors     TEXT NOT NULL DEFAULT '[]',
    abstract    TEXT,
    year        INTEGER,
    pdf_url     TEXT,
    raw_json    TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, content='papers', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract)
    VALUES (new.rowid, new.title, coalesce(new.abstract, ''));
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
    VALUES ('delete', old.rowid, old.title, coalesce(old.abstract, ''));
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
    VALUES ('delete', old.rowid, old.title, coalesce(old.abstract, ''));
    INSERT INTO papers_fts(rowid, title, abstract)
    VALUES (new.rowid, new.title, coalesce(new.abstract, ''));
END;
"""


class PaperCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        logger.debug("cache opened at %s", self.path)

    def close(self) -> None:
        self._conn.close()

    def upsert(self, record: PaperRecord) -> str:
        """Insert or update a record; returns its dedup key."""
        key = record.dedup_key()
        raw = json.dumps({
            "source": record.source,
            "source_id": record.source_id,
            "doi": record.doi,
            "title": record.title,
            "authors": record.authors,
            "abstract": record.abstract,
            "year": record.year,
            "pdf_url": record.pdf_url,
            "extra": record.extra,
        }, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO papers
                (dedup_key, source, source_id, doi, title, authors,
                 abstract, year, pdf_url, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedup_key) DO UPDATE SET
                source=excluded.source,
                source_id=excluded.source_id,
                doi=excluded.doi,
                title=excluded.title,
                authors=excluded.authors,
                abstract=excluded.abstract,
                year=excluded.year,
                pdf_url=excluded.pdf_url,
                raw_json=excluded.raw_json,
                updated_at=datetime('now')
            """,
            (key, record.source, record.source_id, record.doi, record.title,
             json.dumps(record.authors, ensure_ascii=False), record.abstract,
             record.year, record.pdf_url, raw),
        )
        self._conn.commit()
        logger.debug("cache upsert %s (%s)", key, record.source)
        return key

    def upsert_many(self, records: list[PaperRecord]) -> list[str]:
        return [self.upsert(r) for r in records]

    def get_by_key(self, dedup_key: str) -> PaperRecord | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE dedup_key=?", (dedup_key,)
        ).fetchone()
        if row is None:
            logger.debug("cache miss %s", dedup_key)
            return None
        logger.debug("cache hit %s", dedup_key)
        return self._row_to_record(row)

    def search(self, query: str, limit: int = 50) -> list[PaperRecord]:
        """Full-text search over title + abstract."""
        safe_q = " ".join(f'"{t}"' for t in query.split())
        logger.debug("fts query: %s", safe_q)
        rows = self._conn.execute(
            """
            SELECT p.* FROM papers_fts f
            JOIN papers p ON p.rowid = f.rowid
            WHERE papers_fts MATCH ?
            ORDER BY bm25(papers_fts)
            LIMIT ?
            """,
            (safe_q, limit),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def _row_to_record(self, row: sqlite3.Row) -> PaperRecord:
        authors = json.loads(row["authors"] or "[]")
        raw = json.loads(row["raw_json"] or "{}")
        extra = raw.get("extra") or {}
        return PaperRecord(
            source=row["source"],
            source_id=row["source_id"],
            title=row["title"],
            authors=authors,
            doi=row["doi"],
            abstract=row["abstract"],
            year=row["year"],
            pdf_url=row["pdf_url"],
            extra=extra,
        )