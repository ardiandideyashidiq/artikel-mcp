## Context

See proposal.md for motivation. The project is a minimal MCP server (`src/artikel_mcp/`, Python 3.13, only `mcp[cli]` dependency, stdio transport, `ping` + `word_count` tools). Empirical verification was done this session against every target API:

- Global REST sources (Crossref, DOAJ, Europe PMC, HAL, arXiv, PMC/NCBI, Unpaywall) all answer plain curl with JSON. No impersonation needed.
- Semantic Scholar returns consistent 429s — excluded.
- Garuda and SINTA moved from `*.kemdikbud.go.id` (DNS dead) to `*.kemdiktisaintek.go.id`. Garuda serves HTML only (`/documents?q=`, detail pages, exposes direct OJS PDF links). SINTA is an author/journal-tier index, not a document index — dropped per decision.
- Browser impersonation benchmark (primp 2.0.1 vs curl_cffi 0.16.3, Chrome fingerprint): parity on Garuda/SINTA; curl_cffi faster on global APIs and exposes a `requests`-style API.

## Goals / Non-Goals

**Goals:**
- Single normalized `Paper` record across all adapters.
- Cache-first search backed by SQLite + FTS5 so upstream is never re-hit for a known paper.
- A download path that works through bot protection, with Unpaywall as the paywall fallback only.
- Systematic logging on every adapter, fetch, cache op, and extraction; one runnable check per non-trivial module.

**Non-Goals:**
- No vector/embedding store (SQLite FTS5 chosen by user over ChromaDB).
- No RAG/chunking/retrieval tools in this change.
- No bibliography/citation formatting tools.
- No university repositories (UGM, UI, etc.), SINTA, ISJD. Only indexers.
- No Celery/worker queue — PDF work runs synchronously in the MCP server; acceptable at single-user scale. Marked as a known ceiling.
- Semantic Scholar is not integrated.

## Decisions

### One shared `curl_cffi` client with browser impersonation
Single `Client`/`Session` (impersonate Chrome) used by every adapter and the PDF downloader. Verified parity with primp; curl_cffi wins on global API speed and gives a `requests`-compatible surface. primp is a documented fallback driver behind an interface if a future source blocks curl_cffi's fingerprints.

Alternatives: primp (win slightly on one HTML target, negligible), plain `httpx`/`urllib` (fails bot-protected sources). Rationale: one client covers both JSON APIs and scrapers.

### Adapter pattern with a registry
One module per source (`sources/crossref.py`, `sources/arxiv.py`, `sources/garuda.py`, ...), each exposing `search(query) -> list[PaperRecord]`. A registry maps source name -> adapter. `search_papers` fans out over requested sources, catches per-source errors, and tags results with source + source_id.

Note: Garuda's page structure may change; its adapter is a plain-HTML scraper (stdlib `html.parser` or regex on stable selectors), kept isolated so a page change only touches one file.

### Normalized record shape
`PaperRecord`: `source`, `source_id`, `doi` (nullable), `title`, `authors: list[str]`, `abstract` (nullable), `year` (nullable), `pdf_url` (nullable), `extra: dict`. Source adapters map raw JSON/HTML into this. Nullable fields stay null rather than dropping the record.

### SQLite + FTS5 as the cache/dedup layer
Store two tables:
- `papers`: canonical rows keyed by `dedup_key` = DOI if present else `f"{source}:{source_id}"`. Columns mirror `PaperRecord` plus `fetched_at`, `updated_at`, `raw_json`.
- `papers_fts`: FTS5 virtual table over `title`, `abstract`, content synced to `papers` with triggers.

Behavior: `search_papers` runs the FTS query first; hits are returned without upstream calls. On miss, adapters run and every result is upserted (dedup keys from the `09-never-fetch-twice` rule). `download_paper` lookup: local record first, Unpaywall enrichment cached too.

Alternatives: ChromaDB (rejected by user — local vector DB adds embed infra with no need), in-memory dict (lost on restart). Rationale: sqlite3 is stdlib, file-backed, FTS5 is built in.

### Paywall fallback inside download only
The download tool takes a paper (DOI and/or pdf_url). It tries direct fetch through the impersonating client. On failure (non-2xx, non-PDF magic bytes), if a DOI exists it queries Unpaywall (`api.unpaywall.org/v2/{doi}?email=`), then downloads the returned OA location. Unpaywall needs a real email — read from env var `UNPAYWALL_EMAIL`; if unset, the fallback path errors with a clear message. Unpaywall is never a search source.

### PDF extraction: pymupdf → custom cleaner → pymupdf4llm fallback
Primary path: `fitz` (pymupdf) page-wise text extraction, then a custom cleaning pass: strip repeated headers/footers (detected via repetition across pages), merge hyphen/line-wrapped words, rejoin paragraph breaks, lift simple tables to markdown tables. Fallback: if the custom engine output is judged too garbled (low text density, suspiciously short output, or explicit flag), rerun with `pymupdf4llm`. No heavy ML parser (Marker/Nougat/Grobid) in this change — deferred as a known ceiling.

### Modular layout
`src/artikel_mcp/`
- `sources/` — adapter registry + one module per source
- `cache.py` — SQLite/FTS5 open, upsert, FTS query
- `pdf.py` — download (curl_cffi, Unpaywall fallback), extraction, cleaning
- `models.py` — `PaperRecord` (and PDF result shape)
- `server.py` — registers `search_papers`, `download_paper`, `extract_pdf_text`
- `logging.py` — existing; extend as needed

## Risks / Trade-offs

- [Garuda HTML structure changes overnight] → Isolated scraper module; any break fails only that source, not the server; test covers selector assumptions.
- [Garuda/SINTA DNS churn (domains move with ministry reorgs)] → Adapter config takes base URL from env/constant; source-registry test pins current live endpoint.
- [Upstream rate limits / 429s] → Cache-first design minimizes upstream load; per-source error isolation surfaces limits, doesn't crash the search. Optional polite-pool headers (mailto) where accepted (Crossref, Unpaywall).
- [Unpaywall refuses fake emails] → Requires `UNPAYWALL_EMAIL`; the fallback path fails with a clear config error when unset, tested.
- [Bot-protected downloads still blocked even with impersonation] → Provider fingerprints evolve; curl_cffi abstracts rotation. primp remains a documented alternate driver.
- [Two-column / math-heavy PDFs extract poorly with pymupdf alone] → pymupdf4llm fallback covers most; Marker/Nougat deferred — mark ceiling with `ponytail:`-style comment in code.
- [Synchronous PDF extraction blocks the server] → Single-user stdio server; documented ceiling (a queue worker is the upgrade path if throughput matters).
- [FTS5 word-boundary quirks for non-English tokens] → Acceptable for the covered sources; tokenizing is a future refinement.

## Migration Plan

Greenfield addition — no existing data to migrate. Rollback = revert the commits that add the new modules and tool registrations; existing `ping`/`word_count` are untouched throughout.

## Open Questions

- None blocking. Deferred by design until a user asks: chunking/RAG tools, citation formatting, Grobid/Marker integration, multi-user queueing.