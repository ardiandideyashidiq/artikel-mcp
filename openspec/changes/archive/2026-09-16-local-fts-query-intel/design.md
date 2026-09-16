## Context

`PaperCache.search()` builds an FTS5 MATCH expression by quoting every token and AND-joining them (`cache.py:162`). Stopwords like "status" and "di" are included, so a realistic Indonesian query such as "status hukum deepfake di indonesia" requires all five tokens to appear in the same document — returning zero hits even though the cache holds 29 records matching `deepfake* indonesia*`.

Meanwhile `query_broker.adapt()` already strips these exact stopwords and expands synonyms for upstream sources. The local FTS path never sees that transformation.

The change bridges this gap: route the user query through the broker's stopword/synonym logic before building the FTS5 MATCH expression, and add prefix matching to improve recall for inflected terms.

## Goals / Non-Goals

**Goals:**
- Local FTS5 queries use the same stopword and synonym set as upstream sources.
- Cached Indonesian papers are reachable via a realistic multi-token query without falling through to upstream.
- Single-token queries behave identically to the current path.

**Non-Goals:**
- No change to the upstream search fan-out or its concurrency model.
- No change to the PDF pipeline, download path, or public MCP tool signatures.
- No NLP-grade stemming, lemmatization, or language detection — the existing synonym table plus prefix matching is the deliberate ceiling (ponytail: retrofit if recall is still weak after real-world queries).

## Decisions

### 1. Reuse `query_broker.adapt` for local queries

**Choice:** Call `adapt(query, "arxiv")` (or a new public function exposing the common transformation) before passing the adapted string to `cache.search`, instead of reimplementing stopword/synonym logic inside `PaperCache`.

**Rationale:** The broker is the single source of truth for token vocabulary. Duplicating the stopword set in `cache.py` creates a sync risk; extracting the common transform avoids that.

**Alternative considered:** Move the broker into `cache.py`. Rejected because the broker is a pure transformation layer used by `registry.py`; merging it with the persistence layer violates single responsibility and would break the broker's "no network, no state" contract.

### 2. Prefix-match all tokens in the local FTS query

**Choice:** Wrap each adapted token in a prefix match (`tok*`) in the MATCH expression.

**Rationale:** FTS5's unicode61 tokenizer indexes whole tokens. Prefix matching lets "deepfake*" match "deepfakes" and "indonesia*" match "indonesian" — covering the two main inflection patterns in academic Indonesian/English titles without NLP dependencies. Per context7 research, only the trigram tokenizer supports LIKE substring; prefix is the standard recall trick and has no index overhead.

**Alternative considered:** Switch to the trigram tokenizer for substring matching. Rejected because it triples storage and the current synonym expansion already covers the dominant multi-language recall gap.

### 3. `cache.search` signature unchanged

**Choice:** `cache.search(query, limit)` continues to accept a plain string. The adaptation happens in `service.py` before calling `cache.search`, keeping the cache layer transport-agnostic.

**Rationale:** Keeps `PaperCache` reusable for any future query source that does not need broker adaptation.

## Risks / Trade-offs

- **[Over-expansion noise]** Synonym expansion adds OR groups; an aggressive table could return irrelevant local hits. Mitigation: the synonym table is intentionally small and Indonesian-English only; measure local hit precision before adding entries.
- **[Prefix match reduces specificity]** `deepfake*` also matches any token starting with "deepf". Mitigation: academic paper tokens are short and specific; observed false-positive risk is low. If it surfaces, restrict prefix to 4+ character tokens.
- **[Upstream drift]** If someone adds a new stopword to `query_broker._STOPWORDS`, the local path picks it up automatically — but a regression could also silently break local recall. Mitigation: the existing test suite covers both paths; add a local-specific test that asserts the stopwords are stripped.
