## 1. Thread-safe HTTP client

- [x] 1.1 Change `get_client()` in `src/artikel_mcp/http.py` to serve a per-thread `HttpClient` via `threading.local()`, and verify `pytest tests/test_pdf.py` still passes (unaffected callers get a client either way)
- [x] 1.2 Confirm each adapter still resolves its client through `client or get_client()` and add a quick concurrency smoke (two threads calling `get_client()` return distinct instances, same thread returns the same instance)

## 2. Query broker

- [x] 2.1 Add `src/artikel_mcp/query_broker.py` with an identity-default `adapt(query, source) -> str` and verify `python -c "from artikel_mcp.query_broker import adapt; assert callable(adapt)"` succeeds
- [x] 2.2 Implement stopword pruning (small id/en list) + static id-en synonym expansion (incl. `hukum` -> `hukum OR law OR legal`) and verify `pytest tests/` covers: `adapt("status hukum deepfake di indonesia", "arxiv")` drops `status`/`di` and contains the `hukum OR law OR legal` expansion
- [x] 2.3 Implement per-source syntax handling: arxiv `all:` prefix, doaj/crossref expanded keywords, garuda raw user tokens; unknown source raises `ValueError`. Verify with unit tests that each source's adapted string differs only in its own syntax and that calling twice for the same source is byte-identical (determinism, no network)

## 3. Concurrent fan-out in registry

- [x] 3.1 Rework `registry.search_all` in `src/artikel_mcp/sources/registry.py` to submit one task per selected source to a `ThreadPoolExecutor`, per-source query from `query_broker.adapt`, and collect records+errors from `as_completed`; verify existing `tests/test_registry.py::test_isolation_not_aborted_by_failure` and `tests/test_smoke_global.py` stay green
- [x] 3.2 Add a no-network concurrency test: register a `SlowAdapter` that sleeps ~0.3s and a `FastAdapter` that returns immediately, assert the whole `search_all` finishes well under the sum (e.g. < 0.35s) and both sources' records are present
- [x] 3.3 Add a hung-source test: register an adapter whose `search` blocks past the client timeout, assert `search_all` still returns the healthy source's records plus a per-source error for the hung source (wait bounded per D3)

## 4. Service wiring and integration

- [x] 4.1 End-to-end test in `tests/test_mcp_integration.py`: register a `StaticAdapter` plus a slow adapter, call the MCP `search_papers` tool, and verify parallel execution (fast completion) with per-source adapted queries reaching each adapter (assert adapter records the exact query string it received)
- [x] 4.2 Run full suite `uv run pytest` and `uv run ruff check src tests` on every task completion in this section; verify no failures and no new formatting/lint issues

## 5. Verification

- [x] 5.1 Run `uv run pytest -m network tests/test_smoke_global.py` (or the repo's live-smoke command) against real sources for a cache-miss query and confirm `sources_queried` lists all requested sources and `records` are non-empty
- [x] 5.2 Manual smoke: run the MCP server and call `search_papers("status hukum deepfake di indonesia")` with empty local cache; observe response below the sequential-prediction time and that garuda returns Indonesian-language hits while arxiv returns English law/deepfake hits