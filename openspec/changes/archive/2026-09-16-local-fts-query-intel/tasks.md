## 1. Broker reuse for local queries

- [x] 1.1 Add a public `local_adapt(query: str) -> str` function in `query_broker.py` that exposes the stopword/synonym transformation (same logic as `adapt(query, "arxiv")` but without requiring a source name) — verify with `uv run python -c "from artikel_mcp.query_broker import local_adapt; print(local_adapt('status hukum deepfake di indonesia'))"` producing `hukum OR law OR legal deepfake indonesia`

## 2. Local FTS query builder update

- [x] 2.1 Modify `PaperCache.search` to accept an optional `adapted: bool = False` parameter — when `True`, tokens are wrapped as prefix matches (`tok*`) instead of exact quoted phrases; verify existing single-token test `test_fts_matches_abstract_only` still passes unchanged (no `adapted` flag = old behavior)

## 3. Wire adaptation in the service layer

- [x] 3.1 In `service.py:search_papers`, call `local_adapt(query)` before `cache.search` and pass `adapted=True` so the local FTS pass uses prefix matching on adapted tokens — verify with `uv run pytest tests/` that all 29 existing tests pass

## 4. Add local FTS recall test

- [x] 4.1 Add `test_local_fts_recall_multi_token` in `test_cache.py`: upsert records with titles "Deepfake Fraud in Indonesia" and "Legal Status of Deepfakes", call `cache.search("status hukum deepfake di indonesia", adapted=True)`, assert both are returned — verify with `uv run pytest tests/test_cache.py::test_local_fts_recall_multi_token`
