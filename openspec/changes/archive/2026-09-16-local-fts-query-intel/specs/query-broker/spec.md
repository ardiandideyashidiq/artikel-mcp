## ADDED Requirements

### Requirement: Local FTS cache query must be adapted
The local FTS5 cache pass in `search_papers` SHALL apply the same stopword pruning and synonym expansion that the broker applies to upstream sources, so that cached records are reachable via a realistic multi-token query. The adapted local query MUST use prefix matching (`tok*`) on each token to improve recall for inflected or partial terms.

#### Scenario: Realistic Indonesian query hits local cache
- **WHEN** the user queries "status hukum deepfake di indonesia" and the local FTS5 cache already holds records matching "deepfake" and "indonesia"
- **THEN** the local FTS pass returns those cached records as hits without falling through to upstream

#### Scenario: Stopwords are stripped before local MATCH
- **WHEN** the user query contains stopwords ("status", "di", "yang", "dan", etc.)
- **THEN** those tokens are removed before the FTS5 MATCH expression is built, so they do not force a zero-hit AND join

#### Scenario: Single-token query is unchanged
- **WHEN** the query is a single token (e.g. "entanglement")
- **THEN** the adapted local query still matches and behaves identically to the previous quoted-phrase path

### Requirement: Broker `adapt` reuse
The local FTS adaptation MUST reuse the existing `query_broker.adapt` function rather than reimplementing stopword/synonym logic, so the local and upstream paths stay in sync.

#### Scenario: Same query, same stopword set
- **WHEN** the user queries "status hukum deepfake di indonesia"
- **THEN** the local FTS pass strips the same stopwords that `adapt("...", "arxiv")` would strip
