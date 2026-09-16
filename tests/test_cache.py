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
