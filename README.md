# artikel-mcp

MCP server for academic paper workflows: search across multiple scholarly
indexes (global + Indonesian), dedupe via a local SQLite/FTS5 cache, and
download PDFs into clean markdown.

## Tools

- `search_papers(query, sources)` — cache-first search. Matches in the local
  FTS index are served without touching upstream; on a miss the requested
  sources are queried and results persisted (a paper is never fetched twice).
  `sources` defaults to `["local"]` (local-only). Supported upstream sources:
  `crossref`, `arxiv`, `doaj`, `europepmc`, `hal`, `pmc`, `garuda`.
- `download_paper(doi, pdf_url)` — downloads the PDF (direct fetch validated
  by `%PDF` magic bytes), extracts markdown via pymupdf with a pymupdf4llm
  fallback. On paywall/403 failure, falls back to an open-access copy via
  Unpaywall (requires `UNPAYWALL_EMAIL`).

## Requirements

- Python 3.13, `uv` (or `uv run`).
- `UNPAYWALL_EMAIL` (optional, only for the Unpaywall fallback): a real
  contact email; Unpaywall rejects throwaway addresses.

## Environment

- `UNPAYWALL_EMAIL` — email passed to Unpaywall for paywall fallback.
- `ARTIKEL_MCP_DB` — override the SQLite cache path (default:
  platform-dependent user data dir).

## Development

```bash
uv sync            # install deps
uv run pytest      # 29 tests: 21 offline, 8 network-marked live smokes
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Live-source tests are tagged `network` and skipped by default; run them with
`uv run pytest -m network`. They hit real APIs and are flaky when an index is
down (DOAJ's API intermittently 502s; a failed source never aborts the rest).

## Known limits

- Synchronous PDF download/extraction blocks the MCP server during a
  `download_paper` call; a long PDF stalls the session.
- The Garuda adapter parses current Garuda HTML; a redesign of that page may
  break it until selectors are updated.
- Unpaywall is a download-only fallback, never a search source.
- Crew/dup resolution is exact-key based (DOI else `source:source_id`); same
  paper from different sources with different DOIs can appear twice.