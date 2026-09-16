## Purpose

Downloads academic PDFs through bot protection and converts them to clean markdown, using browser impersonation for fetching, pymupdf for extraction, a custom cleaner, and Unpaywall as a paywall fallback.

## ADDED Requirements

### Requirement: Browser-impersonating PDF download
The system SHALL download PDFs using an HTTP client that impersonates a real browser fingerprint so sources protected by bot mitigation (Cloudflare-style) respond normally. Direct PDF URLs MUST be fetched with this client and the resulting bytes validated as a PDF.

#### Scenario: Protected direct download
- **WHEN** a user requests a paper with a direct PDF URL that sits behind bot protection
- **THEN** the system fetches it with browser impersonation and returns the PDF bytes

#### Scenario: Not a PDF response
- **WHEN** the download yields content whose magic bytes are not PDF
- **THEN** the system surfaces a download error rather than returning non-PDF content as a PDF

### Requirement: Paywall fallback via Unpaywall
The system SHALL treat Unpaywall as a fallback only inside the download path: when direct PDF retrieval fails (paywall, 403, or no PDF link), the system SHALL resolve the open-access copy for the paper's DOI via the Unpaywall API, then download the returned OA PDF location. Unpaywall MUST NOT be used as a search or metadata source.

#### Scenario: Direct fetch blocked, OA link found
- **WHEN** direct PDF download fails and Unpaywall returns an OA PDF location for the DOI
- **THEN** the system downloads the OA PDF and returns it with a note that the Unpaywall fallback was used

#### Scenario: Direct fetch blocked, no OA copy
- **WHEN** direct PDF download fails and Unpaywall reports no OA copy
- **THEN** the system returns a clear "no open-access copy available" error

### Requirement: PDF text extraction
The system SHALL extract text from a valid PDF using `pymupdf` and produce markdown output via a custom cleaning engine that removes headers, footers, and page artifacts and normalizes layout (paragraphs, headings, and simple tables).

#### Scenario: Two-column journal PDF
- **WHEN** extraction runs on a two-column journal PDF
- **THEN** the custom engine returns a single logical reading order in markdown rather than interleaved columns