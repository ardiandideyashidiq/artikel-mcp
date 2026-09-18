# AGENTS.md

MCP server for academic paper workflows: multi-index scholarly search, SQLite/FTS5 caching, and PDF-to-Markdown extraction. Python 3.13, managed with `uv`.

## Commands

```bash
uv sync                      # install deps (dev group: pytest, ruff)
uv run pytest                # offline suite (currently 131 tests)
uv run pytest -m network     # live tests hitting real APIs + stdio protocol round-trip
                             #   (test_tool_protocol.py spawns the real server over stdio,
                             #    runs offline-safe calls, and one live CrossRef search)
uv run pytest tests/test_tool_smoke.py -v   # tool/resource/prompt inventory + lifecycle (offline)
uv run pytest -m network tests/test_tool_protocol.py -v  # JSON-RPC over real stdio client
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run artikel-mcp           # run the server (stdio transport); entry src/artikel_mcp/server.py:main
```

Run `ruff check` on every change. Verify with `uv run pytest` before finishing.

## Architecture

- `server.py` defines the MCP tools/resources/prompts; `service.py` holds the application logic both layers share.
- Search is cache-first: `service.search_papers` -> `query_broker.py` (query cleaning, DOI/arXiv-ID detection, natural-language limits) -> `sources/registry.py` fan-out to per-source adapters (`sources/<source>.py`). Records persist to `PaperCache` (SQLite + FTS5, `cache.py`).
- Download path: `service.download_paper` -> `ojs.py` (OJS/DOAJ/Garuda landing-page resolution) and `pdf.py` (download, pymupdf extraction, pymupdf4llm fallback, Unpaywall).

## Gotchas

- **Do not replace the HTTP client.** All network I/O goes through the curl-cffi browser-impersonating client in `http.py` (default `chrome124`). Swapping in requests/urllib/aiohttp breaks 403-bypass against university journals — the core feature.
- **Adding a source/query index**: write an adapter in `sources/<name>.py` and register it in `sources/registry.py`; registry-owned sources get raw queries, everything else goes through `query_broker` adaptation.
- **Offline tests must never hit the network.** Network tests are marked `network` (see `tests/test_smoke_global.py`, `tests/test_ojs.py`). Mock with monkeypatch/FakeClient as in `tests/test_ojs.py:121`.
- Env vars: `ARTIKEL_MCP_DB` overrides DB path (default `~/.local/share/artikel-mcp/papers.db`); `UNPAYWALL_EMAIL` gates the Unpaywall open-access fallback (`pdf.py:49`).
- Logging: use `logging.getLogger("artikel_mcp.<module>")`; `logging.py` installs the handler once.

## OpenSpec workflow

The repo is driven by OpenSpec (`openspec/`). Features/changes go through skills in `.opencode/skills/` — `openspec-propose` -> `openspec-apply-change` -> `openspec-archive-change` (archives to `openspec/changes/archive/`). `openspec/specs/*/spec.md` holds current specs; keep them in sync when behavior changes.