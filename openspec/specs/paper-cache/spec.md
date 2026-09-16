## Purpose

Persists every fetched paper record in a local SQLite database with full-text search, so search is served from the local cache first and the same paper is never fetched from upstream twice.

## Requirements

### Requirement: Persistent record store keyed by stable identifier
The system SHALL store every fetched paper record in a local SQLite database. Each record MUST be keyed by its stable identifier: DOI when present, otherwise the source-specific stable id prefixed with the source name.

#### Scenario: First fetch of a paper
- **WHEN** a paper record is fetched from a source and the key is absent from the database
- **THEN** the record is inserted and the fetch is recorded

#### Scenario: Re-fetch of the same paper
- **WHEN** a paper record is fetched whose key already exists in the database
- **THEN** the existing row is updated in place and no duplicate row is created

### Requirement: Cache-first search on local index
The system SHALL search the SQLite full-text index before contacting upstream sources. A search returning local matches MUST be served without upstream calls.

#### Scenario: Local hit
- **WHEN** a user searches for a term that matches indexed local records
- **THEN** the system returns the local matches and performs no upstream fetch for them

#### Scenario: Local miss with upstream search
- **WHEN** a user searches for a term with no local matches
- **THEN** the system queries the requested upstream sources and persists the newly fetched records

### Requirement: Full-text indexing of titles and abstracts
The system SHALL maintain a SQLite FTS5 index over record title and abstract fields so free-text queries match against stored content.

#### Scenario: Term match on abstract
- **WHEN** a search term appears in a stored record's abstract but not its title
- **THEN** the FTS query returns that record as a match

### Requirement: Storage location is data-friendly
The system SHALL place the SQLite database file on disk in a configurable, user-owned location (defaulting to a standard data directory), never inside the package install directory.

#### Scenario: Default database path
- **WHEN** the server starts without an explicit database path
- **THEN** the database is created under the platform data directory for the application

### Requirement: Record provenance and freshness metadata
The system SHALL store, for each record: fetch time, originating source, and source-specific id, so cached results can be attributed and refreshed later.

#### Scenario: Refresh behavior
- **WHEN** a record exists in the cache and an upstream source returns an updated version
- **THEN** the stored record is replaced with the newer version while retaining its original key