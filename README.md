# artikel-mcp

MCP server for academic paper workflows: multi-index scholarly search (global + Indonesian),
guaranteed structured output, automatic query logging, SQLite/FTS5 caching, and PDF-to-Markdown extraction.

## Features & MCP Tools

- **`search_papers(query, sources, limit, force_refresh)`**:
  - **Natural Query Limits**: Request specific numbers directly in your prompt, e.g. `"cari 50 artikel tentang x"` or `"find 100 papers on y"`. Supports limits up to 200.
  - **Guaranteed 5-Field Standard**: Every single paper record returned contains:
    1. **Title**: Clean academic title
    2. **Authors**: List of authors & formatted author string
    3. **Publication**: Journal name, conference venue, or publisher
    4. **DOI / Link**: Direct clickable resolvable URL
    5. **Research Results & Key Findings**: Distilled findings extracted from abstract
  - **100% Formatting Consistency**: Returns pre-rendered `formatted` blocks per paper and a consolidated `formatted_summary` payload.
  - **Conversational Sanitizer**: Strips conversational filler and queries clean keywords.
  - **Multi-Index Fan-Out**: Queries `crossref`, `arxiv`, `garuda` (Kemdiktisaintek Indonesia), `doaj`, `europepmc`, `hal`, `pmc`, or `local` (SQLite cache).
  - **Indexed Query History**: Logs all searches into `search_queries` and links results via `query_papers`.
- **`download_paper(url, doi, pdf_url, force_fallback)`**:
  - **Open Journal Systems (OJS) & Academic Landing Page Engine**: Automatically resolves OJS article URLs (`/article/view/...`), DOAJ fulltext links, Garuda publisher pages, and DOI redirect targets into direct PDF streams.
  - **Bypasses 403 Forbidden**: Browser-impersonating HTTP fetch via `curl-cffi` avoids anti-bot blocks on Indonesian university journals and academic portals without needing external tools like WebFetch or curl.
  - **Metadata & Cache Auto-Enrichment**: Extracts Highwire (`citation_pdf_url`, `citation_doi`) and Dublin Core tags, persisting discovered metadata, DOI, and Markdown into SQLite and FTS5.
  - Layout-aware Markdown extraction via `pymupdf` with `pymupdf4llm` fallback.
  - Automatic paywall bypass via Unpaywall (requires `UNPAYWALL_EMAIL`).
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

## Architecture

- **Search (cache-first)**: `search_papers` cleans and parses the query (`query_broker.py`), then fans out through a per-source adapter registry (`sources/registry.py`) to `arxiv`, `crossref`, `garuda`, `doaj`, `europepmc`, `hal`, and `pmc`. Every query and result is persisted to SQLite + FTS5 (`cache.py`), so repeat searches are served from the local cache unless `force_refresh` is set.
- **Download**: `download_paper` resolves OJS/DOAJ/Garuda landing pages and DOI redirects (`ojs.py`), then downloads the PDF through a browser-impersonating HTTP client (`curl-cffi`, `http.py`) and extracts Markdown via `pymupdf` with a `pymupdf4llm` fallback (`pdf.py`). DOIs without a direct PDF fall back to Unpaywall.

## Requirements & Environment

- Python 3.13, `uv` (or `uv run`).
- `UNPAYWALL_EMAIL` (optional): email passed to Unpaywall for open-access fallback.
- `ARTIKEL_MCP_DB` (optional): override the SQLite database path (default: `~/.local/share/artikel-mcp/papers.db`).

## Development & Verification

```bash
uv sync            # install dependencies
uv run pytest              # offline suite (currently 57 tests)
uv run pytest -m network   # live tests hitting real APIs (skipped by default)
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

The repo is driven by OpenSpec: specs live in `openspec/specs/` and completed changes are archived in `openspec/changes/archive/`; new work starts via the `openspec-propose` skill.