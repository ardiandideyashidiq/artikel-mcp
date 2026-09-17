## 1. Dependency and parser

- [x] 1.1 Add `bibtexparser` with `uv add bibtexparser` and verify `uv run python -c "import bibtexparser"` succeeds
- [x] 1.2 Create `src/artikel_mcp/bib.py` with `parse_bib_file(path) -> list[PaperRecord]` mapping entries to `PaperRecord(source="bib", source_id=<key>, ...)` with `authors` split on `and`, missing fields null, volume/number/pages in `extra`, and verify by parsing the real file: `uv run python -c "from artikel_mcp.bib import parse_bib_file; r=parse_bib_file('/home/rd/Downloads/howissouthkoreahandlingdeepfake-2026-09-10.bib'); assert len(r)==42; assert len({x.dedup_key() for x in r})<42"`
- [x] 1.3 Write offline tests in `tests/test_bib.py` using a fixture that reproduces the file's quirks (Korean author fields, `#` and quotes in titles, `--` page ranges, missing DOI/abstract, `and`-joined authors) covering: valid file parse, missing/unreadable file error, missing-field entry kept with generated research results, malformed entry skipped without aborting the run; verify `uv run pytest tests/test_bib.py` passes

## 2. Service layer

- [x] 2.1 Extract the 5-part record builder `_as_dict` from `search_papers` to a module-level helper in `service.py` and rewire `search_papers` to call it; verify existing search behavior unchanged with `uv run pytest tests/` (all offline tests pass, no assertion diffs)
- [x] 2.2 Implement `service.ingest_bibliography(cache, bib_path, download=True, limit=200)` that parses the file, upserts every record into the cache, deduplicates by DOI, downloads per entry via `download_paper` (serial, per-entry exception isolation), tags each record with status (`downloaded`/`cached`/`skipped`/`failed`) plus per-entry error, and returns summary counts and a formatted summary; verify with an offline test using a monkeypatched/FakeClient download so no network is hit, covering duplicated-DOI collapse, `download=False`, re-ingest idempotency, and download-failure isolation
- [x] 2.3 Add one `network`-marked test in `tests/test_bib.py` that ingests a small fixture with a real DOI and verifies markdown is returned and persisted; verify `uv run pytest -m network tests/test_bib.py` passes when network is available

## 3. MCP tool registration

- [x] 3.1 Register the `ingest_bibliography(bib_path, download=True, limit=200)` tool in `server.py` with a description explaining it consumes a `.bib` file and runs parse -> normalize -> index -> download -> read; verify the tool is listed by the server tool registration (e.g. via the existing MCP integration test pattern) and `uv run pytest` still passes

## 4. Final verification

- [x] 4.1 Run `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` and fix any findings
- [x] 4.2 Run the full offline suite `uv run pytest` and verify all tests pass
- [x] 4.3 Run one live ingest against the real file (`uv run python` calling `ingest_bibliography` on `/home/rd/Downloads/howissouthkoreahandlingdeepfake-2026-09-10.bib`, `download=True`) and manually verify: 42 entries ingested, duplicate DOIs collapsed, downloaded entries readable via `get_cached_paper(doi)`, DOI-less entries reported `failed` without aborting