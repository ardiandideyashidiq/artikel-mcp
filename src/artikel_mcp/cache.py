"""SQLite + FTS5 persistence layer for fetched paper records."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from artikel_mcp.citation import parse_author_name
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


_TABLES_SCHEMA = """
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
    citekey     TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

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

CREATE TABLE IF NOT EXISTS query_papers (
    query_id        INTEGER NOT NULL,
    paper_key       TEXT NOT NULL,
    rank_position   INTEGER NOT NULL,
    PRIMARY KEY (query_id, paper_key),
    FOREIGN KEY (query_id) REFERENCES search_queries(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_key) REFERENCES papers(dedup_key) ON DELETE CASCADE
);
"""

_INDICES_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_citekey ON papers(citekey);
CREATE INDEX IF NOT EXISTS idx_papers_url ON papers(url);
CREATE INDEX IF NOT EXISTS idx_papers_pdf_url ON papers(pdf_url);
CREATE INDEX IF NOT EXISTS idx_search_queries_created ON search_queries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_queries_raw ON search_queries(raw_query);
CREATE INDEX IF NOT EXISTS idx_query_papers_key ON query_papers(paper_key);
"""

_FTS_OPERATORS = frozenset({"OR", "AND", "NOT"})


class PaperCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA busy_timeout=5000;")
            except Exception as e:
                logger.debug("Failed to set PRAGMA journal_mode: %s", e)
            self._conn.executescript(_TABLES_SCHEMA)
            self._migrate()
            self._conn.executescript(_INDICES_SCHEMA)
        logger.debug("cache opened at %s", self.path)

    def _extract_citekey(self, record: PaperRecord) -> str:
        if record.authors:
            last = parse_author_name(record.authors[0]).last
            clean_last = re.sub(r"\W+", "", last).lower()
        else:
            clean_last = "item"
        year = str(record.year) if record.year else "nodate"
        return f"{clean_last}{year}"

    def _migrate(self) -> None:
        """Ensure columns, citekeys, and FTS definition match current version."""
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(papers)").fetchall()]
        if "markdown" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN markdown TEXT")
        if "url" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN url TEXT")
        if "publication" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN publication TEXT")
        if "research_results" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN research_results TEXT")
        if "citekey" not in cols:
            self._conn.execute("ALTER TABLE papers ADD COLUMN citekey TEXT")
        self._conn.commit()

        # Backfill citekey for existing rows where missing
        rows = self._conn.execute(
            "SELECT rowid, authors, year FROM papers WHERE citekey IS NULL OR citekey = ''"
        ).fetchall()
        if rows:
            updates = []
            for r in rows:
                authors = json.loads(r["authors"] or "[]")
                if authors:
                    last = parse_author_name(authors[0]).last
                    clean_last = re.sub(r"\W+", "", last).lower()
                else:
                    clean_last = "item"
                year = str(r["year"]) if r["year"] else "nodate"
                updates.append((f"{clean_last}{year}", r["rowid"]))
            self._conn.executemany("UPDATE papers SET citekey = ? WHERE rowid = ?", updates)
            self._conn.commit()
            logger.info("backfilled citekey for %d existing cached papers", len(updates))

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
        with self._lock:
            self._conn.close()

    def upsert(self, record: PaperRecord) -> str:
        """Insert or update a record; returns its dedup key."""
        key = record.dedup_key()
        citekey = self._extract_citekey(record)
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
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO papers
                    (dedup_key, source, source_id, doi, title, authors,
                     abstract, year, pdf_url, markdown, url, publication,
                     research_results, raw_json, citekey)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    citekey=coalesce(excluded.citekey, papers.citekey),
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
                    citekey,
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
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE papers
                SET markdown = ?, updated_at = datetime('now')
                WHERE dedup_key = ? OR lower(doi) = ? OR lower(url) = ?
                   OR lower(pdf_url) = ? OR lower(citekey) = ?
                """,
                (markdown, key, key, key, key, key),
            )
            self._conn.commit()
            if cur.rowcount > 0:
                logger.debug("saved markdown for %s", key)
                return True

        # Insert a stub record if the paper was downloaded directly without a prior search record
        doi = key if key.startswith("10.") else None
        # arXiv IDs (raw or arxiv:-prefixed) are stored under source="arxiv" so
        # dedup_key is arxiv:<id> and the service fast-path can find them.
        arxiv_match = re.fullmatch(r"(?:(?:arxiv|arXiv):)?(\d{4}\.\d{4,5}(?:v\d+)?)", key)
        source = "arxiv" if arxiv_match else "direct"
        source_id = arxiv_match.group(1) if arxiv_match else key
        stub = PaperRecord(
            source=source,
            source_id=source_id,
            title=f"Paper ({key})",
            doi=doi,
            markdown=markdown,
        )
        self.upsert(stub)
        logger.debug("inserted stub record with markdown for %s", key)
        return True

    def get_by_key(self, dedup_key: str) -> PaperRecord | None:
        key = dedup_key.lower().strip()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM papers WHERE dedup_key=? OR lower(doi)=? "
                "OR lower(url)=? OR lower(pdf_url)=? OR lower(citekey)=?",
                (key, key, key, key, key),
            ).fetchone()
            # arXiv-ID-shaped keys are also stored under the arxiv: prefix
            if row is None and re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", key):
                row = self._conn.execute(
                    "SELECT * FROM papers WHERE dedup_key=?",
                    (f"arxiv:{key}",),
                ).fetchone()
            if row is None:
                logger.debug("cache miss %s", key)
                return None
            logger.debug("cache hit %s", key)
            return self._row_to_record(row)

    def delete(self, dedup_key: str) -> bool:
        """Delete a paper record by dedup_key, DOI, URL, or PDF URL. Returns True if deleted."""
        key = dedup_key.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM papers WHERE dedup_key=? OR lower(doi)=? "
                "OR lower(url)=? OR lower(pdf_url)=? OR lower(citekey)=?",
                (key, key, key, key, key),
            )
            self._conn.commit()
            deleted = cur.rowcount > 0
            if deleted:
                logger.info("deleted paper record matching %s", key)
            return deleted

    def list_all(self, limit: int = 100, offset: int = 0) -> list[PaperRecord]:
        """List papers from SQLite cache ordered by updated_at descending."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM papers ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            if query and query.strip():
                escaped = (
                    query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                pat = f"%{escaped}%"
                rows = self._conn.execute(
                    """
                    SELECT * FROM search_queries
                    WHERE raw_query LIKE ? ESCAPE '\\' OR cleaned_query LIKE ? ESCAPE '\\'
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
        with self._lock:
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
        clean = re.sub(r'["\'*^:(){}\[\]+\-~]', " ", query)
        terms = [
            t.strip()
            for t in clean.split()
            if len(t.strip()) > 1 and t.strip().upper() not in _FTS_OPERATORS
        ]
        if not terms:
            return []
        if adapted:
            safe_q = " OR ".join(f"{t}*" for t in terms)
        else:
            safe_q = " ".join(f'"{t}"' for t in terms)
        logger.debug("fts query: %s", safe_q)
        with self._lock:
            try:
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
            except sqlite3.OperationalError as e:
                logger.warning("FTS search failed for query %r: %s", query, e)
                return []

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
