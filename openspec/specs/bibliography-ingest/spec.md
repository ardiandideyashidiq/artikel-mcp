## Purpose

Lets users ingest a BibTeX reference file so every entry is normalized into the canonical paper record, indexed in the local cache, and made fully readable through the existing download pipeline.

## Requirements

### Requirement: Ingest a BibTeX file into normalized records
The system SHALL expose an `ingest_bibliography` tool that accepts the path to a `.bib` file, parses its entries, and maps each entry to the canonical normalized paper record shape (`source`, `source_id`, `title`, `authors`, `doi`, `url`, `publication`, `abstract`, `year`, `research_results`) using the same normalization rules as search results. Every parsed entry MUST be indexed into the local SQLite cache.

#### Scenario: Valid bibliography file
- **WHEN** a user calls `ingest_bibliography` with the path of a valid `.bib` file
- **THEN** every entry is returned as a normalized record and indexed in the cache, with a per-entry status reported

#### Scenario: Missing or unreadable file
- **WHEN** the file path does not exist or cannot be read
- **THEN** the tool returns an error naming the path instead of silently producing empty results

#### Scenario: Entry without abstract or DOI
- **WHEN** an entry lacks an abstract or DOI
- **THEN** the entry is still indexed with those fields null and gets a generated research-results summary, not dropped

#### Scenario: Malformed entry does not abort the file
- **WHEN** one entry in the file fails to parse while others succeed
- **THEN** the failed entry is skipped, a per-entry error is reported, and the remaining entries are ingested normally

### Requirement: Deduplicate by stable identifier on ingest
The system SHALL collapse entries that share the same DOI into a single cached record, using the same deduplication keyed by DOI that search uses, so ingest never creates duplicate rows for one paper.

#### Scenario: Duplicate DOIs in one file
- **WHEN** a `.bib` file contains multiple entries with the same DOI
- **THEN** the cache holds exactly one record for that DOI and the tool reports the collision as a deduplicated entry

### Requirement: Downloaded records remain fully readable
The system SHALL route each ingested entry through the existing PDF-to-markdown download pipeline when download is enabled (default), persisting the extracted markdown to the cache so the paper can be re-read later via `get_cached_paper` without re-downloading.

#### Scenario: Download enabled with resolvable DOI or URL
- **WHEN** `ingest_bibliography` runs with `download` enabled (default) and an entry has a DOI or URL
- **THEN** the entry's full text is downloaded through the existing pipeline and its markdown is persisted and reported as available

#### Scenario: Download disabled
- **WHEN** `ingest_bibliography` runs with `download=False`
- **THEN** entries are indexed only, no network download is attempted, and no markdown is fetched

#### Scenario: Download failure isolated per entry
- **WHEN** one entry's download fails while others succeed
- **THEN** the failed entry is reported with its per-entry error and the remaining entries complete normally

#### Scenario: Re-ingest is idempotent
- **WHEN** `ingest_bibliography` is called again on the same file after a successful run
- **THEN** entries already cached with full text are reported as cached and are not re-downloaded

### Requirement: Per-entry result report
The system SHALL return, for every entry, its normalized metadata, a clickable link, the 5-part formatted presentation used by search results, and its download status (`downloaded`, `cached`, `skipped`, `failed`) with an error message when applicable.

#### Scenario: Mixed outcome batch
- **WHEN** a single ingest run produces downloaded, cached, and failed entries
- **THEN** the summary lists each entry with its status, and includes a count of successes and failures

### Requirement: Full CRUD for cached bibliography records
The system SHALL expose tools to manually create (`add_paper`), update metadata or markdown notes (`update_paper`), and remove (`delete_paper`) papers from the local SQLite cache and FTS5 search index.

#### Scenario: Manual paper creation and update
- **WHEN** a user adds a manual paper entry or updates an existing paper's notes/metadata
- **THEN** the record is stored in SQLite and instantly retrievable and searchable via FTS5

#### Scenario: Paper deletion
- **WHEN** a user deletes a paper by DOI or key
- **THEN** the paper is purged from both the papers table and the FTS5 virtual table

### Requirement: Citation formatting with academic styles
The system SHALL format paper citations in standard styles including APA 7th, Chicago (Author-Date and Notes/Bibliography), IEEE, MLA 9th, Harvard, and BibTeX, supporting both full reference entries and in-text parenthetical and narrative citations.

#### Scenario: Generate APA 7th or Chicago citation
- **WHEN** a user requests a citation for a cached paper in APA 7th or Chicago style
- **THEN** the system outputs properly styled bibliographic references and in-text citation keys

### Requirement: Standardized LaTeX source and PDF compilation
The system SHALL export papers into structured LaTeX documents (.tex) and compile them to PDF using system compilers (`pdflatex` or `xelatex`) using predefined templates (`academic`, `review`, `brief`) or user-supplied custom templates to ensure consistent document layout.

#### Scenario: Export paper to academic PDF
- **WHEN** a user exports a paper with `template="academic"` and `compile_pdf=True`
- **THEN** the system generates the LaTeX source and compiles a PDF document with consistent typography, metadata banner, abstract, and references

### Requirement: In-file document citation management and auto-sync bibliography
The system SHALL scan, insert, and remove citation markers in markdown, text, or LaTeX document files, and automatically generate and synchronize the document's formatted references section (in styles such as APA 7th, Chicago, IEEE, MLA 9th) along with an optional companion `.bib` file.

#### Scenario: Scan citations in document
- **WHEN** a user calls `scan_citations` on a manuscript file
- **THEN** all Pandoc markers (`[@key]`, `@key`), HTML comments, and DOIs are identified and resolved against the local database

#### Scenario: Insert citation and auto-sync references
- **WHEN** a user inserts a citation into a document
- **THEN** the citation marker is placed at the specified line or body position and the references section is updated automatically

