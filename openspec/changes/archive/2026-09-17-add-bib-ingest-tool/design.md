## Context

See proposal.md - Why. The server already owns the full normalize/index/download/read chain that the tool must reuse:

- `PaperRecord.__post_init__` fills `url`, `research_results`, and `publication` from minimal fields; `dedup_key()` is `doi.lower()` or `source:source_id` (models.py:83-99).
- `PaperCache.upsert` upserts by `dedup_key` (DOI collisions collapse) and maintains the FTS index via triggers (cache.py:194-255).
- `service.download_paper` resolves direct PDF -> OJS -> OpenAlex -> Unpaywall, persists markdown, and short-circuits on already-cached markdown (service.py:360-525).
- The 5-part presentation formatter `_as_dict` currently lives nested inside `search_papers` (service.py:231-261).
- bibtexparser is NOT installed (verified via import check); a hand-rolled parser is the only alternative.

## Goals / Non-Goals

**Goals:**
- One MCP tool covering parse -> normalize -> index -> download -> read for a `.bib` file.
- Zero new normalization/download/cache logic; every step delegates to existing machinery.
- Idempotent re-runs and per-entry error isolation so one bad entry never aborts the file.

**Non-Goals:**
- No new search tool or query abstraction.
- No upstream enrichment of missing abstracts (Crossref lookup on ingest) - entries are indexed with what the file provides.
- No changes to `search_papers`, `download_paper`, cache schema, or source adapters.
- No CLI or non-MCP entry point.

## Decisions

**1. Add `bibtexparser` as a runtime dependency.**
Verified not installed; added via `uv add bibtexparser`. Rationale: BibTeX is a real format (nested braces, LaTeX escapes, `and`-joined author lists, multi-line values); the file at hand already exercises quotes-in-titles and Korean fields. Alternative rejected: a hand-rolled `re`-based parser (the ponytail default) would be ~40 fragile lines that need maintenance for each new real-world file. Use the current v2 API (`bibtexparser.parse_string`); verify the exact field-access API during apply against the actual file before writing tests.

**2. New module `src/artikel_mcp/bib.py`: file -> records.**
`parse_bib_file(path) -> list[PaperRecord]` maps each entry: `source="bib"`, `source_id=<bibtex entry key>`, `title`/`authors`/`journal->publication`/`year`/`doi`/`url`/`abstract`. `authors` splits on the `and` delimiter. Fields absent from `PaperRecord` (volume, number, pages) go into `extra`. Missing fields stay null and `PaperRecord.__post_init__` fills the normalizers - that IS the "use current provider to normalize" step. Missing/unreadable file and malformed entries raise/skip per spec.

**3. Index through `cache.upsert` per entry - dedup is free.**
Each record's `dedup_key` is its DOI lowercased, so the file's 21 duplicate `*2` entries collapse into one row each. Non-DOI entries key on `bib:<key>` and remain distinct. No uniqueness logic in the tool.

**4. Download through `service.download_paper` per entry, serial, isolated.**
For each entry with a DOI or URL, call `download_paper(cache, doi=..., url=...)` and catch exceptions per entry, so a failed download (expected for the DOI-less kyobobook entries) reports as `failed` and the rest proceed. `download_paper`'s cached-markdown short-circuit makes re-runs return `cached` with no network work. Serial (not concurrent) keeps the network footprint and per-entry logging predictable for a batch tool.

**5. Extract the view formatter for reuse.**
Move `_as_dict` (the 5-part `formatted` builder) from the body of `search_papers` to a module-level helper in `service.py`, called with the same args by both `search_papers` and the new service. Pure refactor; no output change.

**6. Tool shape.**
`ingest_bibliography(bib_path, download=True, limit=200)` in `server.py` delegating to `service.ingest_bibliography(cache, ...)`. Returns per-record view dicts tagged with `status` (`downloaded` / `cached` / `skipped` / `failed`) and per-record `error`, plus summary counts and a `formatted_summary` matching the search presentation so the existing SERVER_INSTRUCTIONS presentation contract holds. `get_cached_paper` re-reads ingested full text by DOI afterwards.

## Risks / Trade-offs

- [bibtexparser v2 API drift (fields vs `fields_dict`)] -> Verify during apply on the real file before writing tests; offline test uses a fixture reproducing the file's quirks.
- [Batch download is a long network run (21+ unique papers)] -> `download=False` and `limit` give users index-only or partial runs; idempotency means interrupted runs resume.
- [DOI-less entries (kyobobook) always fail download] -> Expected; reported per-entry as `failed`, never aborting the run.
- [Hand-rolled assumption wrong about file quirks] -> The offline fixture is drawn from the actual file (Korean fields, `#` in titles, `--` page ranges), so parse coverage is tested, not assumed.

## Migration Plan

Additive: one new dependency, one new module, one new service function, one new tool. Nothing existing changes behavior. Rollback is removing the tool registration and the dependency.

## Open Questions

None - dependency and download-default decisions were confirmed by the user.