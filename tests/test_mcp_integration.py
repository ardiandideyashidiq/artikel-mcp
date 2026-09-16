"""MCP tool integration tests: cache-first search and never-fetch-twice.

These use a temp sqlite cache and the "local" pseudo-source so no upstream
network calls are made; the never-fetch-twice check counts upstream calls.
"""

import pytest

from artikel_mcp.cache import PaperCache
from artikel_mcp.models import PaperRecord
from artikel_mcp.service import search_papers


class CountingCache(PaperCache):
    def __init__(self, path):
        super().__init__(path)
        self.upsert_calls = 0

    def upsert(self, record):
        self.upsert_calls += 1
        return super().upsert(record)

    def upsert_many(self, records):
        self.upsert_calls += len(records)
        return [self.upsert(r) for r in records]


@pytest.fixture()
def cache(tmp_path):
    c = CountingCache(tmp_path / "search.db")
    yield c
    c.close()


def test_cache_first_serves_local_hit_without_upstream(cache):
    rec = PaperRecord(
        source="crossref",
        source_id="zz",
        title="Quantum Attention Networks",
        abstract="a paper about quantum attention mechanisms",
        doi="10.9/zz",
    )
    cache.upsert(rec)

    result = search_papers(cache, "attention", sources=["local"])
    assert result["from_local"] is True
    assert len(result["records"]) == 1
    assert result["records"][0]["title"] == "Quantum Attention Networks"
    assert cache.upsert_calls == 1  # nothing fetched upstream


def test_never_fetch_twice_second_run_is_all_local(cache, monkeypatch):
    # force a "local miss" upstream path with a single fake source so we can
    # count persistence without hitting the network
    from artikel_mcp.sources import registry

    class StaticAdapter:
        name = "static"

        def search(self, query, limit=20):
            return [
                PaperRecord(
                    source="static",
                    source_id="s1",
                    title="Static Test Paper",
                    abstract="full text index of static test paper",
                )
            ]

    monkeypatch.setitem(registry._REGISTRY, "static", StaticAdapter)

    first = search_papers(cache, "needle", sources=["static"])
    assert first["from_local"] is False
    assert len(first["records"]) == 1
    upserts_after_first = cache.upsert_calls

    second = search_papers(cache, "static", sources=["static"])
    assert second["from_local"] is True
    assert cache.upsert_calls == upserts_after_first  # zero upstream re-fetch


def test_mcp_tool_search_papers_cross_thread(tmp_path):
    """MCP tool runs on a worker thread; the sqlite handle must survive it."""
    import asyncio

    from artikel_mcp.cache import PaperCache
    from artikel_mcp.server import create_server

    db = tmp_path / "server.db"
    cache = PaperCache(db)
    cache.upsert(
        PaperRecord(
            source="arxiv",
            source_id="a1",
            title="Topic Matching Paper",
            abstract="contains a uniquely searchable token xyzzy",
        )
    )
    cache.close()

    srv = create_server(db_path=str(db))

    async def run():
        res = await srv.call_tool("search_papers", {"query": "xyzzy", "sources": ["local"]})
        return res

    result = asyncio.run(run())
    assert result is not None
    assert result.is_error is False
