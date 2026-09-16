# artikel-mcp

MCP server for academic paper workflows: multi-index scholarly search (global + Indonesian),
automatic query logging and indexing, SQLite/FTS5 full-text caching, and PDF-to-Markdown extraction.

## Features & MCP Tools

- **`search_papers(query, sources, limit, force_refresh)`**:
  - Automatically cleans conversational phrasing (`"tolong carikan jurnal tentang..."`, `"find papers on..."`).
  - Detects explicit DOIs and arXiv IDs.
  - Cache-first with smart backfill: hits in local FTS5 are served immediately; on cache miss, requests upstream sources in parallel and persists results.
  - Supported upstream sources: `crossref`, `arxiv`, `garuda` (Kemdiktisaintek Indonesia), `doaj`, `europepmc`, `hal`, `pmc`, `local` (local cache only).
  - Automatically logs every query into the `search_queries` table and links returned papers.
- **`download_paper(doi, pdf_url, force_fallback)`**:
  - Downloads the PDF using browser-impersonating `curl-cffi`.
  - Extracts clean Markdown via `pymupdf` (with `pymupdf4llm` layout-fallback).
  - Falls back to Unpaywall if paywalled or 403 (requires `UNPAYWALL_EMAIL`).
  - **Permanently stores extracted Markdown into SQLite and FTS5** for instant re-reading and full-text search.
- **`get_cached_paper(doi_or_key)`**:
  - Instantly inspects full metadata and cached Markdown of any previously downloaded or cached paper.
- **`get_search_history(query, limit)`**:
  - Reviews past search queries, parameters, timestamps, and result counts from SQLite.

## MCP Resources & Prompts

- **Resources**:
  - `paper://{+key}` — Full paper metadata and cached Markdown by DOI or dedup_key.
  - `queries://recent` — JSON list of recent search queries and their result counts.
- **Prompts**:
  - `literature_review(topic)` — Step-by-step literature review workflow for agents.
  - `summarize_paper(doi_or_title)` — Structured paper summarization guide.

## Requirements

- Python 3.13, `uv` (or `uv run`).
- `UNPAYWALL_EMAIL` (optional, only for Unpaywall fallback): a real contact email.

## Environment

- `UNPAYWALL_EMAIL` — email passed to Unpaywall for open-access fallback.
- `ARTIKEL_MCP_DB` — override the SQLite database path (default: platform-dependent user data dir, e.g. `~/.local/share/artikel-mcp/papers.db`).

## Development & Verification

```bash
uv sync            # install dependencies
uv run pytest      # 48 tests (offline suite)
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Live-source tests are tagged `network` and skipped by default; run them with `uv run pytest -m network`.