## Why

`search_papers` queries up to 7 sources sequentially (`registry.py:59-67`), so wall time is the *sum* of every source's latency (7-35s), and a single raw query string is sent verbatim to every source regardless of its syntax or language affinity — hurting recall for Indonesian queries on English-oriented indexes like arxiv.

## What Changes

- Run upstream source queries **concurrently** instead of sequentially, so total wall time tracks the slowest source, not the sum. Failure isolation is preserved: one slow or failing source must not delay or kill the others.
- Provide each source with a **source-adapted query** via a lightweight query broker: stopword pruning, synonym expansion (e.g. `hukum` -> `hukum OR law`), and source-appropriate syntax (arxiv boolean, crossref keywords, garuda raw Indonesian).
- Keep the cache-first contract, normalized record shape, per-source error surfacing, and the MCP tool signature unchanged.

## Capabilities

### New Capabilities

- `query-broker`: Transforms a single user query into one adapted query per source, applying stopword pruning, synonym expansion, and source-specific query syntax so each index receives a query tuned to its own strength.

### Modified Capabilities

- `source-integrations`: Adds a requirement that requested upstream sources are queried concurrently with strict failure isolation, and that each source receives its adapted query string; the unified-search and record-normalization requirements are otherwise unchanged.

## Impact

- `src/artikel_mcp/sources/registry.py` — `search_all` becomes concurrent fan-out; per-source query resolution.
- `src/artikel_mcp/sources/base.py` — optional query-coercion hook on `SourceAdapter` (default identity).
- `src/artikel_mcp/http.py` — thread-local/per-call `HttpClient` instances (curl_cffi `Session` is not thread-safe).
- New `src/artikel_mcp/query_broker.py` — query transformation logic.
- `src/artikel_mcp/service.py` — unchanged contract; relies on `registry.search_all` behavior.
- Tests: registry concurrency/isolation, broker transformation, per-source query routing; existing tests must stay green.
- No new dependencies (stdlib `concurrent.futures`).