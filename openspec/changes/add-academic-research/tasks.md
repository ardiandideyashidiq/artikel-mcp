## 1. Foundation

- [x] 1.1 Add `curl-cffi`, `pymupdf`, `pymupdf4llm` to `pyproject.toml` deps and verify `uv sync --locked` succeeds cleanly
- [x] 1.2 Create `models.py` with `PaperRecord` dataclass and verify a unit test round-trips a record with nullable fields unset
- [x] 1.3 Create shared HTTP client module wrapping `curl_cffi` with Chrome impersonation and verify a smoke test fetches `api.crossref.org` status 200
- [x] 1.4 Wire per-module debug logging through the existing `logging.py` and verify logs stream to stderr with module names

## 2. Cache layer (SQLite + FTS5)

- [x] 2.1 Implement `cache.py`: DB open (configurable path, default platform data dir), `papers` + `papers_fts` tables with upsert triggers, and verify schema creation test passes
- [x] 2.2 Implement dedup upsert keyed on DOI else `source:source_id` and verify a unit test re-fetching the same key produces one row (no duplicate)
- [x] 2.3 Implement FTS5 query over title/abstract and verify a test matches on abstract-only terms
- [x] 2.4 Implement `get_by_key` lookup for the download path and verify a test returns the stored record or None

## 3. Global source adapters (one at a time)

- [x] 3.1 Implement Crossref adapter (works?query=) and verify a live smoke test returns normalized records with DOI and abstract
- [x] 3.2 Implement arXiv adapter (export.arxiv.org/api) and verify a live smoke test parses Atom XML into records (DOI null is OK)
- [x] 3.3 Implement DOAJ adapter (api/v3/search/articles) and verify a live smoke test returns records with `pdf_url`
- [x] 3.4 Implement Europe PMC adapter (ebi.ac.uk webservices) and verify a live smoke test returns records
- [x] 3.5 Implement HAL adapter (api.archives-ouvertes.fr/search) and verify a live smoke test returns records
- [x] 3.6 Implement PMC adapter (NCBI E-utilities esearch+esummary) and verify a live smoke test returns records
- [x] 3.7 Implement source registry + `search_papers` fan-out with per-source error isolation and verify a test calling two sources (one down) still returns healthy results plus an error marker

## 4. Garuda adapter (indexer, HTML)

- [x] 4.1 Implement Garuda scraper against `garuda.kemdiktisaintek.go.id/documents?q=` and verify a live smoke test extracts titles, detail ids, and direct OJS PDF links
- [x] 4.2 Confirm existing selectors hold against the live page and verify the adapter's fail-fast behavior surfaces a clear error when the page structure is unrecognizable (run against a real page)

## 5. PDF pipeline

- [x] 5.1 Implement `pdf.py` download using the impersonating client, validating PDF magic bytes, and verify a test on a known-good PDF URL returns bytes with `%PDF` header
- [x] 5.2 Implement non-PDF rejection and verify a test with a non-PDF URL raises a download error rather than returning junk
- [x] 5.3 Implement pymupdf extraction + custom cleaner (header/footer strip, hyphen rejoin, paragraph merge) and verify cleaning tests pass on built-in fixture text
- [x] 5.4 Implement pymupdf4llm fallback trigger (garbled/short output) and verify the fallback path executes on a synthetic bad-extraction fixture
- [x] 5.5 Implement Unpaywall fallback resolution inside download (env `UNPAYWALL_EMAIL`; resolve OA location on direct-fetch failure) and verify: (a) missing env errors clearly, (b) mocked 403 direct fetch falls through to Unpaywall

## 6. MCP integration

- [x] 6.1 Register `search_papers` tool returning cache-first results (local FTS hit served without upstream) and verify via MCP client test: seeded record matches without network, unknown term triggers upstream
- [x] 6.2 Register `download_paper` tool (DOI and/or pdf_url; direct then Unpaywall fallback) and verify an end-to-end test downloads a real OA PDF and extracts markdown with no exceptions
- [x] 6.3 Verify all adapters persist every fetched record to the cache on miss (never-fetch-twice) and that re-running search for the same query hits FTS only — assert zero upstream calls on second run
- [ ] 6.4 Run `uv run ruff` lint clean and a final live smoke of all seven sources + download; commit the assembled change set with a conventional message

## 7. Docs & polish

- [x] 7.1 Update README with supported sources, env vars (`UNPAYWALL_EMAIL`), DB path override, and known ceilings (sync extraction, no RAG/citations yet)
- [x] 7.2 Add `ponytail:` ceiling comments at the two documented limits (sync PDF work, isolated Garuda scraper) and verify a final full test run passes