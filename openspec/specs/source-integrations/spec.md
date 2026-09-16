## Purpose

Integrates academic discovery sources into the MCP server as pluggable adapters that return normalized paper records for unified search.

## Requirements

### Requirement: Unified paper search across configured sources
The system SHALL expose a `search_papers` tool that accepts a query and a list of source names, queries each requested source, and returns normalized paper records. The source set MUST include: `crossref`, `doaj`, `europepmc`, `hal`, `arxiv`, `pmc`, and `garuda`. Semantic Scholar MUST NOT be queried.

#### Scenario: Search across multiple sources
- **WHEN** a user calls `search_papers` with query "deep learning" and sources `["crossref", "arxiv"]`
- **THEN** the system returns paper records from both sources, each carrying a source name, stable source id, title, authors, abstract when available, publication metadata, and any resolvable PDF link

#### Scenario: Unsupported source rejected
- **WHEN** a user calls `search_papers` with a source name outside the supported set
- **THEN** the system returns an error naming the unsupported source and lists the supported source names

#### Scenario: Multi-source result labels
- **WHEN** records for the same paper appear from multiple sources
- **THEN** each record retains its originating source and source-specific stable id so deduplication can be decided downstream by the caller

### Requirement: Normalized paper record shape
The system SHALL map each source's raw output to a single normalized record shape: `source`, `source_id`, `doi` (nullable), `title`, `authors` (list of names), `abstract` (nullable), `year` (nullable), `pdf_url` (nullable), and `metadata` (source-specific extras).

#### Scenario: Record with missing fields
- **WHEN** a source returns a record without an abstract or DOI (e.g. an arXiv entry without DOI)
- **THEN** the system still returns the record with those fields explicitly null rather than dropping the record

#### Scenario: Record mapping errors surfaced
- **WHEN** a source returns malformed data that cannot be mapped to the normalized shape
- **THEN** the system skips that single record, logs the mapping failure, and continues with the remaining records

### Requirement: Behavioral disruption handling
The system SHALL continue serving other sources when one source fails, and MUST surface per-source errors without aborting the whole search.

#### Scenario: One source down
- **WHEN** `search_papers` is called and one requested source returns a network error or non-2xx status while others succeed
- **THEN** the system returns successful results from the healthy sources and the records for the failed source include a structured error marker

#### Scenario: Garuda's HTML-only interface
- **WHEN** the `garuda` source is searched
- **THEN** the system uses Garuda's HTML search page (`garuda.kemdiktisaintek.go.id`) to extract paper records, since Garuda exposes no JSON API