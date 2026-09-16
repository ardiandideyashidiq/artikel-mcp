## Context

Current flow (see proposal.md - Why): `service.search_papers` (service.py:25) falls through to `registry.search_all` (sources/registry.py:43), which iterates sources in a `for` loop and calls each blocking adapter synchronously. Each adapter builds a `PaperRecord` list from a browser-impersonating `HttpClient` (curl_cffi `Session`) whose default timeout is 20s.

Two constraints shape the design:

- curl_cffi `Session` is not thread-safe; the shared `get_client()` singleton (http.py:90) cannot be reused across threads.
- The `SourceAdapter.search(query, limit)` signature is fixed and each adapter embeds its own query syntax (arxiv `all:` prefix, doaj keyword URL path, crossref `query` param, garuda raw HTML `?q=`).

## Goals / Non-Goals

**Goals:**
- Source queries run concurrently; worst-case wall time is bounded by a single source's timeout, not the sum.
- Each source receives a source-adapted query string.
- No new runtime dependencies; cache-first contract, MCP tool signatures, and normalized record shape unchanged.

**Non-Goals:**
- Full async rewrite of `HttpClient` and adapters (no asyncio event-loop dependency; stdio MCP transport is single-user).
- ML/NLP-grade query expansion; a small static synonym/stopword table suffices for recall.
- Changing search ranking, dedup, or pagination behavior.
- Per-source timeout tuning beyond the existing per-request HTTP timeout.

## Decisions

### D1: Concurrent fan-out with `concurrent.futures.ThreadPoolExecutor`
`registry.search_all` submits one task per selected source, then collects results from `as_completed`.

- **Why threads over asyncio**: adapters, `HttpClient`, and `curl_cffi` are all synchronous; threads reuse them as-is. An async rewrite would touch `http.py`, all 7 adapters, `registry.py`, `service.py`, and the MCP handlers for the same wall-time win.
- **Why `ThreadPoolExecutor` over manual threads**: bounded pool, exception capture via futures, and `as_completed` naturally supports partial results.
- **Why not `ProcessPoolExecutor`**: pure I/O-bound wait; process overhead buys nothing and would require pickling `HttpClient`.
- Only the source-task threads spawn for the duration of one fan-out; max_workers is bounded by the number of requested sources (<= 7).

### D2: Per-thread `HttpClient` via `threading.local()`
`get_client()` becomes thread-local: each thread lazily creates its own curl_cffi session.

- **Alternative rejected**: constructing a fresh `HttpClient` per fan-out task. Works, but leaves the shared singleton as a latent thread-safety bug for any future concurrent caller. Thread-local fixes the root cause once (http.py:87-94), and existing adapters keep using `client or get_client()` unchanged.
- Consequence: each source thread gets one new TCP keep-alive session. Negligible for 7 concurrent requests.

### D3: Wait bound = existing per-request HTTP timeout
Each adapter's `HttpClient` already times out at 20s (http.py:38). Because every source runs concurrently, total fan-out wall time is at most the slowest request's timeout (~20s worst case, ~1-5s typical) — satisfying the "hung source cannot indefinitely delay" requirement without adding a second timeout mechanism.

- **Alternative rejected**: a global fan-out deadline with future cancellation (`as_completed(timeout=...)`). Adds lifecycle complexity; curl_cffi's timeout already bounds every request. If a future source ever needs a tighter ceiling, add one `FANOUT_TIMEOUT` knob then.

### D4: Standalone `query_broker.py`, not an adapter hook
The broker owns all per-source adaptation as `adapt(query, source) -> str`, with a static table mapping source name to a transform function and an identity default. `registry.search_all` computes the per-source query before each `adapter.search(q, limit)` call.

- **Why a standalone module over a `SourceAdapter.coerce_query` hook**: keeps source-specific query knowledge in one place that is pure, deterministic, offline-testable (matches the spec's determinism requirements), and leaves the `SourceAdapter.search` interface untouched.
- The proposal's hook idea was superseded: turning it into a routing step in `registry.search_all` is a smaller diff and keeps adapters JSON-parse-only.
- Transform set:
  - **Stopword pruning**: small Indonesian + English list (`status`, `di`, `yang`, `the`, `of`, ...) applied before expansion.
  - **Synonym expansion**: static id-en pairs (e.g. `hukum` -> `hukum OR law OR legal`), applied with `OR` joins so either language matches.
  - **Source syntax**: arxiv wrapper `all:<query>`; doaj and crossref pass expanded keywords as-is; garuda keeps the user's raw tokens (Indonesian affinity, no over-translation).

### D5: Record contract and failure isolation preserved
`search_all` still returns `(records, errors)`. A future that raised (adapter failure) is converted to a per-source error string exactly as today (registry.py:65-67). Record order becomes completion order rather than registry order — acceptable; the spec and callers do not promise ordering. `cache.upsert_many` runs after fan-out completes, on the calling thread, so the shared SQLite connection is never used from source threads.

## Risks / Trade-offs

- [7 concurrent requests to upstream APIs may trip rate limits] -> Sources are called only on a local-cache miss (cache-first), so storms are rare for a single user. Crossref uses a polite pool; keep default `limit` modest. No change to politeness headers.
- [Garuda scraper selectors are DOM-coupled] -> Unchanged code path; its query string is the only thing that changes (raw tokens preserved). Existing selector smoke tests still guard it.
- [Thread-local `HttpClient` adds one session per source thread] -> Up to 7 short-lived keep-alive connections per cache-miss search. Acceptable; sessions close with thread/GC.
- [Result ordering becomes non-deterministic across sources] -> Completion order. Callers already treat records as an unordered bag keyed by source; no documented ordering contract exists in specs.
- [Broker expansion may over-match English synonyms for Indonesian queries] -> Static, conservative table; garuda path keeps raw tokens. If precision suffers, prune the table — a data tweak, not a code change.

## Migration Plan

No data migration. The change is additive to the search path:
1. Land `query_broker.py` and thread-local `get_client()`.
2. Swap `registry.search_all` body from sequential loop to thread fan-out.
3. All existing tests + broker/fan-out tests green; manual smoke per source.
Rollback: revert the three touched modules; behavior returns to sequential raw-query search with zero data impact.

## Open Questions

None. Any tuning (timeout values, stopword lists, expansion pairs) is deferred and data-driven — changing it later does not alter specs or architecture.