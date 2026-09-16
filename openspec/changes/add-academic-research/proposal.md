## Why

The server currently only has `ping` and `word_count` — not an academic research tool. Researchers and agents need a single MCP surface to discover, cache, and read scholarly papers across global open-access indexes and Indonesia's national indexer (Garuda), with PDF text extraction that works through bot protection. Sources must be cached so the same paper is never fetched twice.

## What Changes

- **BREAKING**: none — new capabilities only; existing `ping`/`word_count` tools remain untouched.
- Add a unified `search_papers` tool that queries a set of academic sources and returns normalized paper records.
- Global REST sources (JSON): Crossref, DOAJ, Europe PMC, HAL, arXiv, PubMed Central (NCBI E-utilities). Semantic Scholar is explicitly **out** (reliable 429s).
- Indonesian indexer: Garuda (`garuda.kemdiktisaintek.go.id`, HTML scrape; `kemdikbud.go.id` domains are dead). SINTA and university repositories are explicitly **out**.
- SQLite + FTS5 cache layer: normalized records persisted, de-duplicated on DOI/stable source id, FTS-indexed for local search; search is cache-first, upstream only on miss.
- PDF pipeline: browser-impersonating download (`curl_cffi`), text extraction (`pymupdf`), custom markdown cleaner, fallback to `pymupdf4llm`.
- `download_paper` tool: fetch PDF directly when available; if the article is paywalled/blocked, resolve an OA link via Unpaywall and fetch that instead.
- One shared HTTP client built on `curl_cffi` with browser impersonation; `primp` kept as a documented fallback driver only if a source blocks curl_cffi's fingerprints.
- Systematic logging on every adapter, fetch, cache hit/miss, and extraction.

## Capabilities

### New Capabilities

- `source-integrations`: Adapters for Crossref, DOAJ, Europe PMC, HAL, arXiv, PMC, and Garuda that turn raw API/HTML responses into a normalized `Paper` record; unified search across a requested source set.
- `paper-cache`: SQLite + FTS5 persistence layer; de-duplication on DOI/stable id, cache-first search, "never fetch the same paper twice".
- `pdf-pipeline`: Browser-impersonating PDF download, `pymupdf` extraction, markdown cleaning, `pymupdf4llm` fallback, and Unpaywall fallback resolution for paywalled articles inside the download tool.

### Modified Capabilities

- None (no existing requirements change).

## Impact

- `pyproject.toml`: add `curl-cffi`, `pymupdf`, `pymupdf4llm` (fallback), and stdlib additions only (sqlite3 via stdlib).
- `src/artikel_mcp/`: new modules for sources, cache, pdf pipeline; `server.py` registers new tools.
- New tools registered on the MCP server: `search_papers`, `download_paper`, plus PDF text extraction/cleaning helpers.
- External services consumed: api.crossref.org, doaj.org, ebi.ac.uk/europepmc, api.archives-ouvertes.fr, export.arxiv.org, eutils.ncbi.nlm.nih.gov, garuda.kemdiktisaintek.go.id, api.unpaywall.org (requires a real email for polite pool).