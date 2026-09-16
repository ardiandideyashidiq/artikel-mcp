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
    markdown    TEXT,
    url         TEXT,
    publication TEXT,
    research_results TEXT,
    raw_json    TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, markdown, content='papers', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract, markdown)
    VALUES (new.rowid, new.title, coalesce(new.abstract, ''), coalesce(new.markdown, ''));
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, markdown)
    VALUES ('delete', old.rowid, old.title, coalesce(old.abstract, ''), coalesce(old.markdown, ''));
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, markdown)
    VALUES ('delete', old.rowid, old.title, coalesce(old.abstract, ''), coalesce(old.markdown, ''));
    INSERT INTO papers_fts(rowid, title, abstract, markdown)
    VALUES (new.rowid, new.title, coalesce(new.abstract, ''), coalesce(new.markdown, ''));
END;

CREATE TABLE IF NOT EXISTS search_queries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_query       TEXT NOT NULL,
    cleaned_query   TEXT NOT NULL,
    sources         TEXT NOT NULL,
    results_count   INTEGER NOT NULL DEFAULT 0,
    from_local      BOOLEAN NOT NULL DEFAULT 0,
    duration_ms     REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_search_queries_created ON search_queries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_queries_raw ON search_queries(raw_query);

CREATE TABLE IF NOT EXISTS query_papers (
    query_id        INTEGER NOT NULL,
    paper_key       TEXT NOT NULL,
    rank_position   INTEGER NOT NULL,
    PRIMARY KEY (query_id, paper_key),
    FOREIGN KEY (query_id) REFERENCES search_queries(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_key) REFERENCES papers(dedup_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_query_papers_key ON query_papers(paper_key);
"""

_FTS_OPERATORS = frozenset({"OR", "AND", "NOT"})


class PaperCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: MCP runs tool calls on a worker thread.
        # sqlite serializes writes via its internal mutex; short transactions
        # and a single stdio user make cross-thread reuse safe enough.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        logger.debug("cache opened at %s", self.path)

    def _migrate(self) -> None:
        """Ensure columns and FTS definition match current version."""
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(papers)").fetchall()]
        if "markdown" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN markdown TEXT")
        if "url" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN url TEXT")
        if "publication" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN publication TEXT")
        if "research_results" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN research_results TEXT")
        self._conn.commit()

        fts_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(papers_fts)").fetchall()]
        if "markdown" not in fts_cols:
            self._conn.executescript("""
                DROP TRIGGER IF EXISTS papers_ai;
                DROP TRIGGER IF EXISTS papers_ad;
                DROP TRIGGER IF EXISTS papers_au;
                DROP TABLE IF EXISTS papers_fts;
                CREATE VIRTUAL TABLE papers_fts USING fts5(
                    title, abstract, markdown, content='papers', content_rowid='rowid'
                );
                INSERT INTO papers_fts(rowid, title, abstract, markdown)
                    SELECT rowid, title, coalesce(abstract, ''), coalesce(markdown, '')
                    FROM papers;
                CREATE TRIGGER papers_ai AFTER INSERT ON papers BEGIN
                    INSERT INTO papers_fts(rowid, title, abstract, markdown)
                    VALUES (
                        new.rowid,
                        new.title,
                        coalesce(new.abstract, ''),
                        coalesce(new.markdown, '')
                    );
                END;
                CREATE TRIGGER papers_ad AFTER DELETE ON papers BEGIN
                    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, markdown)
                    VALUES (
                        'delete',
                        old.rowid,
                        old.title,
                        coalesce(old.abstract, ''),
                        coalesce(old.markdown, '')
                    );
                END;
                CREATE TRIGGER papers_au AFTER UPDATE ON papers BEGIN
                    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, markdown)
                    VALUES (
                        'delete',
                        old.rowid,
                        old.title,
                        coalesce(old.abstract, ''),
                        coalesce(old.markdown, '')
                    );
                    INSERT INTO papers_fts(rowid, title, abstract, markdown)
                    VALUES (
                        new.rowid,
                        new.title,
                        coalesce(new.abstract, ''),
                        coalesce(new.markdown, '')
                    );
                END;
            """)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(self, record: PaperRecord) -> str:
        """Insert or update a record; returns its dedup key."""
        key = record.dedup_key()
        raw = json.dumps(
            {
                "source": record.source,
                "source_id": record.source_id,
                "doi": record.doi,
                "title": record.title,
                "authors": record.authors,
                "abstract": record.abstract,
                "year": record.year,
                "pdf_url": record.pdf_url,
                "url": record.url,
                "publication": record.publication,
                "research_results": record.research_results,
                "extra": record.extra,
            },
            ensure_ascii=False,
        )
        self._conn.execute(
            """
            INSERT INTO papers
                (dedup_key, source, source_id, doi, title, authors,
                 abstract, year, pdf_url, markdown, url, publication, research_results, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedup_key) DO UPDATE SET
                source=excluded.source,
                source_id=excluded.source_id,
                doi=excluded.doi,
                title=excluded.title,
                authors=excluded.authors,
                abstract=excluded.abstract,
                year=excluded.year,
                pdf_url=excluded.pdf_url,
                markdown=coalesce(excluded.markdown, papers.markdown),
                url=coalesce(excluded.url, papers.url),
                publication=coalesce(excluded.publication, papers.publication),
                research_results=coalesce(excluded.research_results, papers.research_results),
                raw_json=excluded.raw_json,
                updated_at=datetime('now')
            """,
            (
                key,
                record.source,
                record.source_id,
                record.doi,
                record.title,
                json.dumps(record.authors, ensure_ascii=False),
                record.abstract,
                record.year,
                record.pdf_url,
                record.markdown,
                record.url,
                record.publication,
                record.research_results,
                raw,
            ),
        )
        self._conn.commit()
        logger.debug("cache upsert %s (%s)", key, record.source)
        return key

    def upsert_many(self, records: list[PaperRecord]) -> list[str]:
        return [self.upsert(r) for r in records]

    def upsert_markdown(self, dedup_key: str, markdown: str) -> bool:
        """Save extracted markdown for a paper by DOI or dedup_key."""
        key = dedup_key.lower().strip()
        cur = self._conn.execute(
            """
            UPDATE papers
            SET markdown = ?, updated_at = datetime('now')
            WHERE dedup_key = ? OR lower(doi) = ?
            """,
            (markdown, key, key),
        )
        self._conn.commit()
        if cur.rowcount > 0:
            logger.debug("saved markdown for %s", key)
            return True

        # Insert a stub record if the paper was downloaded directly without a prior search record
        doi = key if key.startswith("10.") else None
        stub = PaperRecord(
            source="direct",
            source_id=key,
            title=f"Paper ({key})",
            doi=doi,
            markdown=markdown,
        )
        self.upsert(stub)
        logger.debug("inserted stub record with markdown for %s", key)
        return True

    def get_by_key(self, dedup_key: str) -> PaperRecord | None:
        key = dedup_key.lower().strip()
        row = self._conn.execute(
            "SELECT * FROM papers WHERE dedup_key=? OR lower(doi)=?",
            (key, key),
        ).fetchone()
        if row is None:
            logger.debug("cache miss %s", key)
            return None
        logger.debug("cache hit %s", key)
        return self._row_to_record(row)

    def log_query(
        self,
        raw_query: str,
        cleaned_query: str,
        sources: list[str],
        results_count: int,
        from_local: bool,
        duration_ms: float = 0.0,
    ) -> int:
        """Record a search query in search_queries table; returns query id."""
        cur = self._conn.execute(
            """
            INSERT INTO search_queries
                (raw_query, cleaned_query, sources, results_count, from_local, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                raw_query,
                cleaned_query,
                json.dumps(sources),
                results_count,
                1 if from_local else 0,
                round(duration_ms, 2),
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore

    def link_query_papers(self, query_id: int, paper_keys: list[str]) -> None:
        """Associate a query with the resulting papers."""
        if not paper_keys:
            return
        rows = [(query_id, key, idx + 1) for idx, key in enumerate(paper_keys)]
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO query_papers (query_id, paper_key, rank_position)
            VALUES (?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()

    def get_recent_queries(self, query: str | None = None, limit: int = 20) -> list[dict]:
        """Retrieve recent search queries, optionally filtered by keyword."""
        if query and query.strip():
            pat = f"%{query.strip()}%"
            rows = self._conn.execute(
                """
                SELECT * FROM search_queries
                WHERE raw_query LIKE ? OR cleaned_query LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (pat, pat, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM search_queries
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "raw_query": r["raw_query"],
                    "cleaned_query": r["cleaned_query"],
                    "sources": json.loads(r["sources"] or "[]"),
                    "results_count": r["results_count"],
                    "from_local": bool(r["from_local"]),
                    "duration_ms": r["duration_ms"],
                    "created_at": r["created_at"],
                }
            )
        return out

    def get_papers_for_query(self, query_id: int) -> list[PaperRecord]:
        """Retrieve papers returned by a specific query ID."""
        rows = self._conn.execute(
            """
            SELECT p.* FROM papers p
            JOIN query_papers qp ON p.dedup_key = qp.paper_key
            WHERE qp.query_id = ?
            ORDER BY qp.rank_position ASC
            """,
            (query_id,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def search(self, query: str, limit: int = 50, adapted: bool = False) -> list[PaperRecord]:
        """Full-text search over title, abstract, and cached markdown content."""
        if adapted:
            terms = [t for t in query.split() if t.upper() not in _FTS_OPERATORS]
            safe_q = " OR ".join(f"{t}*" for t in terms) if terms else query
        else:
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
        row_keys = set(row.keys())
        markdown = row["markdown"] if "markdown" in row_keys else None
        url = row["url"] if "url" in row_keys else None
        publication = row["publication"] if "publication" in row_keys else None
        research_results = row["research_results"] if "research_results" in row_keys else None
        return PaperRecord(
            source=row["source"],
            source_id=row["source_id"],
            title=row["title"],
            authors=authors,
            doi=row["doi"],
            url=url,
            publication=publication,
            abstract=row["abstract"],
            research_results=research_results,
            year=row["year"],
            pdf_url=row["pdf_url"],
            markdown=markdown,
            extra=extra,
        )
