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