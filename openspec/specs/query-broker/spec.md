## Purpose

Transforms a single user query into one source-adapted query per academic index so each upstream source receives a query tuned to its own syntax and language affinity.

## Requirements

### Requirement: Per-source query adaptation
The system SHALL derive a distinct adapted query string for each requested upstream source from the user's original query. The adaptation MUST prune low-information words, expand synonyms so a query in one language can match documents in another, and apply any source-appropriate query syntax. A source MUST NEVER receive a query adapted for a different source.

#### Scenario: Indonesian query on English-oriented index
- **WHEN** a user searches "status hukum deepfake di indonesia" and the fan-out includes arxiv
- **THEN** the arxiv query removes stopwords and expands `hukum` to its synonym set so the English index matches legal/law content, while the adapted string stays a valid arxiv `all:` query

#### Scenario: Native-language source keeps raw keywords
- **WHEN** the fan-out includes a source with low English affinity (e.g. garuda)
- **THEN** that source receives a query that preserves the user's original terms so Indonesian-language results are not lost to over-translation

#### Scenario: Unknown source
- **WHEN** the broker is asked to adapt a query for a source outside the supported set
- **THEN** the broker raises/returns the unsupported-source error and no other source is affected

### Requirement: Deterministic adaptation
The system SHALL produce the same adapted query for the same (source, raw query) input on every invocation, with no network calls or external state, so results are reproducible and the transformation is unit-testable offline.

#### Scenario: Repeatability
- **WHEN** the same raw query is adapted twice for the same source
- **THEN** both adapted strings are identical

#### Scenario: Offline testability
- **WHEN** a test invokes the broker for any supported source with a fixed query
- **THEN** no network request is made and the resulting string depends only on the source and query

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

### Requirement: Broker adapt reuse
The local FTS adaptation MUST reuse the existing `query_broker.adapt` function rather than reimplementing stopword/synonym logic, so the local and upstream paths stay in sync.

#### Scenario: Same query, same stopword set
- **WHEN** the user queries "status hukum deepfake di indonesia"
- **THEN** the local FTS pass strips the same stopwords that `adapt("...", "arxiv")` would strip