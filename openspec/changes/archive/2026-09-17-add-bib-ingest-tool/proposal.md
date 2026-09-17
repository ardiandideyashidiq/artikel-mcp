## Why

Exporting a reference library (e.g. from Zotero/Google Scholar) produces a `.bib` file, but the server has no entry point to consume it: 42 entries must currently be searched and downloaded one-by-one. A single tool that ingests the whole file through the existing normalize/index/download/read pipeline lets a user hand over a bibliography and get every paper indexed and readable in one call.

## What Changes

- Add bibtexparser as a runtime dependency.
- Add a new MCP tool `ingest_bibliography(bib_path, download=True, limit)` that:
  - parses the `.bib` file into entries,
  - normalizes each entry to the existing `PaperRecord` shape (`source="bib"`),
  - indexes records into the SQLite cache (dedup by DOI, FTS-indexed),
  - downloads full-text markdown per entry through the existing `download_paper` pipeline when `download` is enabled (default),
  - returns per-entry 5-part formatted records plus download status and per-entry errors.
- Re-run is idempotent: entries already cached with markdown are skipped without network work.
- Existing behavior of `search_papers`, `download_paper`, `get_cached_paper`, and the source adapters is unchanged.

## Capabilities

### New Capabilities
- `bibliography-ingest`: Ingest a BibTeX file, normalize its entries to the canonical paper record shape, index them into the local cache, and make their full text downloadable through the existing pipeline.

### Modified Capabilities

## Impact

- Code: `src/artikel_mcp/bib.py` (new parser/mapper), `src/artikel_mcp/service.py` (new `ingest_bibliography` service; extract nested `_as_dict` formatter to module level for reuse), `src/artikel_mcp/server.py` (new tool registration), `tests/test_bib.py` (new offline tests; network-marked download test).
- Dependency: `bibtexparser` added via `uv add`.
- Data: writes into the existing SQLite cache (`papers` table) via `PaperCache.upsert`; no schema change.
- Docs/specs: new `openspec/specs/bibliography-ingest/spec.md`; `AGENTS.md` commands unchanged.