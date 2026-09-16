## ADDED Requirements

### Requirement: Concurrent upstream fan-out
The system SHALL query requested upstream sources concurrently so no source waits for another to complete, and SHALL bound the wait so a slow or hung source cannot indefinitely delay the result. Completed sources MUST be returned even when another source fails or times out.

#### Scenario: Multi-source search completes concurrently
- **WHEN** `search_papers` is called with multiple upstream sources and the local FTS index has no match
- **THEN** all requested sources are queried concurrently, records from every completed source are returned, and total wall time tracks the slowest completing source rather than the sum of all sources

#### Scenario: Hung source does not block results
- **WHEN** one requested source exceeds the fan-out wait bound while the others respond promptly
- **THEN** the search returns the completed sources' records and the slow source is reported as a per-source error, with no impact on the other sources' results

#### Scenario: Per-source adapted query
- **WHEN** `search_papers` fans out to multiple upstream sources
- **THEN** each source receives the query string produced by the query broker for that source, and the adapted string is not the raw user query verbatim when the source's adaptation differs from identity