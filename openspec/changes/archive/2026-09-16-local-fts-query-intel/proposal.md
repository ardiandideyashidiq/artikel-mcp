## Why

When an agent calls `search_papers`, the local FTS5 cache pass runs *before* upstream. But `cache.search` builds an AND-join of every quoted token (including stopwords like "status" and "di") so the cache-first hit rate is zero for realistic Indonesian queries. The agent then falls through to upstream, hits the 50 KB truncation cap, and falls back to `websearch` (exa) and `rg` — both of which bypass the MCP tools entirely.

The query-broker already strips stopwords and expands synonyms for upstream sources, but the local FTS pass never sees that transformation. The cache already holds the answer; the query builder just cannot reach it.

## What Changes

- The local FTS5 query path in `cache.search` applies the same stopword stripping and synonym expansion that the query broker applies to upstream sources, so cached records are reachable via a realistic query.
- FTS5 query tokens use prefix matching (`tok*`) on the final token of each phrase to improve recall for inflected or partial terms.
- No change to the upstream search paths, PDF pipeline, or public MCP tool signatures.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-broker`: local FTS cache query adaptation — the broker's stopword and synonym logic now also covers the local FTS5 pass, and prefix matching is applied so cached Indonesian papers are reachable.

## Impact

- `src/artikel_mcp/cache.py` — `PaperCache.search()` query builder changes.
- `src/artikel_mcp/service.py` — `search_papers` routes the local pass through adapted query.
- `tests/test_cache.py` — new tests for multi-token local FTS recall; existing single-token test unchanged.
