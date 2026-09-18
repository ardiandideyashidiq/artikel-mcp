"""Tests for the SQLite + FTS5 paper cache."""

import pytest

from artikel_mcp.cache import PaperCache
from artikel_mcp.models import PaperRecord


@pytest.fixture()
def cache(tmp_path):
    c = PaperCache(tmp_path / "cache.db")
    yield c
    c.close()


def test_schema_creates(cache):
    tables = cache._conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
    ).fetchall()
    names = {t["name"] for t in tables}
    assert {"papers", "papers_fts", "papers_ai", "papers_ad", "papers_au"} <= names


def test_dedup_upsert_single_row(cache):
    r = PaperRecord(source="crossref", source_id="a1", title="Paper", doi="10.1000/abc")
    k1 = cache.upsert(r)
    k2 = cache.upsert(r)
    assert k1 == k2
    count = cache._conn.execute("SELECT count(*) FROM papers").fetchone()[0]
    assert count == 1


def test_doi_preferred_over_source_prefix(cache):
    r1 = PaperRecord(source="crossref", source_id="x", title="A", doi="10.1/X")
    r2 = PaperRecord(source="arxiv", source_id="y", title="B", doi="10.1/X")
    cache.upsert(r1)
    cache.upsert(r2)
    assert cache._conn.execute("SELECT count(*) FROM papers").fetchone()[0] == 1


def test_fts_matches_abstract_only(cache):
    rec = PaperRecord(
        source="arxiv",
        source_id="2101.00001",
        title="Mild Title",
        abstract="Quantum entanglement is the key concept here",
    )
    cache.upsert(rec)
    hits = cache.search("entanglement")
    assert len(hits) == 1
    assert hits[0].source_id == "2101.00001"


def test_get_by_key(cache):
    r = PaperRecord(source="doaj", source_id="d42", title="T")
    cache.upsert(r)
    assert cache.get_by_key("doaj:d42") is not None
    assert cache.get_by_key("doaj:missing") is None


def test_local_fts_recall_multi_token(cache):
    cache.upsert(
        PaperRecord(
            source="arxiv",
            source_id="a1",
            title="Deepfake Fraud in Indonesia",
            abstract="survey of deepfake fraud detection in southeast Asia",
        )
    )
    cache.upsert(
        PaperRecord(
            source="arxiv",
            source_id="a2",
            title="Legal Status of Deepfakes",
            abstract="regulation and legal analysis of deepfakes",
        )
    )
    hits = cache.search("status hukum deepfake di indonesia", adapted=True)
    assert len(hits) == 2
    assert {h.source_id for h in hits} == {"a1", "a2"}


def test_cache_migration_from_old_schema_without_citekey(tmp_path):
    """Ensure opening an old database without citekey column migrates and backfills cleanly."""
    import sqlite3

    db_file = tmp_path / "legacy_papers.db"
    conn = sqlite3.connect(db_file)
    # Create legacy schema missing citekey
    conn.execute(
        """
        CREATE TABLE papers (
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
        """
    )
    conn.execute(
        """
        INSERT INTO papers (dedup_key, source, source_id, title, authors, year)
        VALUES ('legacy:1', 'manual', '1', 'Legacy Paper', '["John Doe"]', 2021);
        """
    )
    conn.commit()
    conn.close()

    # Opening with PaperCache must not crash and must migrate columns, backfill citekey, and index
    cache = PaperCache(db_file)
    paper = cache.get_by_key("legacy:1")
    assert paper is not None
    assert paper.title == "Legacy Paper"

    # Verify citekey was backfilled and is queryable
    paper_by_citekey = cache.get_by_key("doe2021")
    assert paper_by_citekey is not None
    assert paper_by_citekey.title == "Legacy Paper"
    cache.close()
