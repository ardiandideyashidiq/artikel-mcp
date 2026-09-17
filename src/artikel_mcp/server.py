import json
import logging
from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

from artikel_mcp.cache import PaperCache
from artikel_mcp.logging import setup_logging
from artikel_mcp.service import add_paper as service_add_paper
from artikel_mcp.service import delete_paper as service_delete_paper
from artikel_mcp.service import download_paper as service_download
from artikel_mcp.service import export_bibliography_file as service_export_bib
from artikel_mcp.service import export_paper_document as service_export_paper
from artikel_mcp.service import format_paper_citation as service_format_citation
from artikel_mcp.service import get_cached_paper as service_get_cached
from artikel_mcp.service import get_search_history as service_history
from artikel_mcp.service import ingest_bibliography as service_ingest
from artikel_mcp.service import insert_document_citation as service_insert_citation
from artikel_mcp.service import remove_document_citation as service_remove_citation
from artikel_mcp.service import scan_document_citations as service_scan_citations
from artikel_mcp.service import search_papers as service_search
from artikel_mcp.service import sync_document_bibliography as service_sync_bibliography
from artikel_mcp.service import update_paper as service_update_paper
from artikel_mcp.sources import registry

logger = logging.getLogger("artikel_mcp")

SERVER_INSTRUCTIONS = """You are connected to artikel-mcp, an academic research server.

RECOMMENDED WORKFLOWS:
1. Search Papers: Use `search_papers` to query global and Indonesian databases (arXiv,
   CrossRef, OpenAlex, Semantic Scholar, Garuda, PubMed, DOAJ, Europe PMC, HAL, PMC) or local
   SQLite cache. Supports year ranges and natural language limits.
2. Download & Read Full Text: Use `download_paper` when a DOI or paper URL (including OJS, DOAJ,
   and Garuda landing pages) is known. Automatically extracts clean Markdown. For token
   efficiency on long papers, specify `summary_only=True` or a page range (`page_start`,
   `page_end`, `max_pages`).
3. Cached Papers & Citations: Use `get_cached_paper` to re-read cached papers without network
   latency, and `format_citation` or `export_paper` for standard academic formatting.
"""


def create_server(db_path: str | None = None) -> MCPServer:
    setup_logging()
    cache = PaperCache(db_path)
    mcp = MCPServer(
        "artikel-mcp",
        title="Artikel MCP Server",
        description=(
            "Comprehensive academic research MCP server: multi-index search, "
            "SQLite FTS5 caching, and PDF-to-Markdown extraction."
        ),
        instructions=SERVER_INSTRUCTIONS,
    )

    @mcp.tool(
        description=(
            "Search across global and Indonesian academic indexes (arXiv, CrossRef, Garuda, "
            "Google Scholar, DOAJ, EuropePMC, HAL, PMC, OpenAlex, PubMed, Semantic Scholar) "
            "and local FTS cache. Automatically persists all results and indexes queries into "
            "SQLite. Returns guaranteed title, authors, publication, DOI/link, and research "
            "results. USE THIS TOOL whenever the user asks for academic papers, journals, "
            "literature reviews, or research on any topic."
        )
    )
    def search_papers(
        query: Annotated[
            str,
            Field(
                description=(
                    "The research topic, question, title, keywords, DOI (e.g. '10.1038/...'), "
                    "or arXiv ID. You can also specify counts, e.g. 'cari 50 artikel tentang x'."
                )
            ),
        ],
        source: Annotated[
            str | None,
            Field(
                description=(
                    "Optional single index name or 'all' to query all sources (default 'all', "
                    "which includes Google Scholar with auto-rotating proxies): 'all', 'scholar', "
                    "'arxiv', 'crossref', 'garuda' (Indonesian portal), 'doaj', 'europepmc', "
                    "'hal', 'pmc', 'openalex', 'pubmed', 'semantic', or 'local' "
                    "(local database only)."
                )
            ),
        ] = "all",
        sources: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional list of indexes to query (e.g. ['scholar', 'arxiv'] or ['all']). "
                    "Overrides 'source' if explicitly provided."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                description=(
                    "Maximum number of papers to return (default 10, max 200). "
                    "Can also be requested directly in query, e.g. 'cari 50 artikel tentang x'."
                ),
                ge=1,
                le=200,
            ),
        ] = 10,
        force_refresh: Annotated[
            bool,
            Field(
                description="If True, bypasses local cache and fetches fresh upstream results.",
            ),
        ] = False,
        year_min: Annotated[
            int | None,
            Field(
                description="Optional minimum publication year filter (e.g. 2020).",
            ),
        ] = None,
        year_max: Annotated[
            int | None,
            Field(
                description="Optional maximum publication year filter (e.g. 2025).",
            ),
        ] = None,
        format_mode: Annotated[
            str,
            Field(
                description=(
                    "Output format mode: 'both' (default, includes records and summary), "
                    "'records' (structured list only), or 'summary' (compact formatted text only)."
                ),
            ),
        ] = "both",
    ) -> dict:
        """Search academic indexes and return normalized paper records."""
        return service_search(
            cache,
            query,
            sources=sources,
            source=source,
            limit=limit,
            force_refresh=force_refresh,
            year_min=year_min,
            year_max=year_max,
            format_mode=format_mode,
        )

    @mcp.tool(
        description=(
            "Download an academic paper's PDF and extract clean, structured Markdown. "
            "Automatically resolves Open Journal Systems (OJS) article pages, DOAJ links, "
            "Garuda publisher pages, DOI redirect targets, and Unpaywall open-access copies. "
            "Saves the extracted Markdown permanently into local SQLite for instant re-reading "
            "and full-text search. USE THIS TOOL whenever you need to read or analyze "
            "the full content of a paper with a DOI, article URL, or PDF URL."
        )
    )
    def download_paper(
        url: Annotated[
            str | None,
            Field(
                description=(
                    "Direct URL to the paper's PDF or article landing page (e.g. OJS "
                    "'/article/view/...', DOAJ link, Garuda publisher page, repository)."
                )
            ),
        ] = None,
        doi: Annotated[
            str | None,
            Field(
                description=(
                    "Digital Object Identifier of the paper (e.g. '10.1038/s41586-020-2649-2' "
                    "or 'https://doi.org/...')."
                )
            ),
        ] = None,
        pdf_url: Annotated[
            str | None,
            Field(description="Direct URL to the paper's PDF file (synonym for url)."),
        ] = None,
        force_fallback: Annotated[
            bool,
            Field(
                description=(
                    "If True, forces secondary extraction (pymupdf4llm) instead of primary parser."
                )
            ),
        ] = False,
        page_start: Annotated[
            int,
            Field(
                description="Starting page for text extraction (0-indexed, default 0).",
                ge=0,
            ),
        ] = 0,
        page_end: Annotated[
            int | None,
            Field(
                description="Ending page for text extraction (optional, non-inclusive).",
            ),
        ] = None,
        max_pages: Annotated[
            int,
            Field(
                description="Maximum number of pages to extract (default 100).",
                ge=1,
                le=200,
            ),
        ] = 100,
        summary_only: Annotated[
            bool,
            Field(
                description=(
                    "If True, returns a compact 2,000-character preview instead of full markdown, "
                    "saving context window tokens on long documents."
                ),
            ),
        ] = False,
    ) -> dict:
        """Download a paper's PDF, return extracted markdown, and persist it to SQLite."""
        return service_download(
            cache,
            doi=doi,
            url=url,
            pdf_url=pdf_url,
            force_fallback=force_fallback,
            page_start=page_start,
            page_end=page_end,
            max_pages=max_pages,
            summary_only=summary_only,
        )

    @mcp.tool(
        description=(
            "Ingest a BibTeX (.bib) reference file: parse every entry, normalize it to the "
            "canonical paper record, index it into the local SQLite cache (deduplicated by DOI), "
            "and by default download each entry's full text to Markdown through the same pipeline "
            "as download_paper. Returns per-entry records with the 5-part formatted presentation, "
            "a download status (downloaded/cached/skipped/failed), and an error when one occurs. "
            "USE THIS TOOL when the user provides a .bib file or a reference-library export to "
            "index and read in bulk."
        )
    )
    def ingest_bibliography(
        bib_path: Annotated[
            str,
            Field(
                description=(
                    "Path to the .bib file to ingest (e.g. '/home/user/refs.bib'). "
                    "Entries are deduplicated by DOI when indexed."
                )
            ),
        ],
        download: Annotated[
            bool,
            Field(
                description=(
                    "If True (default), download and extract full text for each entry through "
                    "the existing PDF pipeline. Set False to index only."
                )
            ),
        ] = True,
        limit: Annotated[
            int,
            Field(
                description="Maximum number of entries to ingest (default 200).",
                ge=1,
                le=1000,
            ),
        ] = 200,
    ) -> dict:
        """Ingest a BibTeX file, index its entries, and download full text."""
        return service_ingest(cache, bib_path, download=download, limit=limit)

    @mcp.tool(
        description=(
            "Retrieve cached paper details and extracted full-text Markdown from local SQLite "
            "without re-downloading. USE THIS TOOL when you want to re-examine or quote from a "
            "paper that was previously downloaded or cached."
        )
    )
    def get_cached_paper(
        doi_or_key: Annotated[
            str,
            Field(
                description=(
                    "The DOI (e.g. '10.1038/...') or internal dedup_key "
                    "(e.g. 'arxiv:2301.12345') of the paper."
                )
            ),
        ],
        full_text: Annotated[
            bool,
            Field(
                description=(
                    "If True (default), includes cached full-text markdown. "
                    "Set False for metadata only."
                ),
            ),
        ] = True,
        max_chars: Annotated[
            int | None,
            Field(
                description="Optional character cap on cached markdown to protect context windows.",
            ),
        ] = None,
    ) -> dict:
        """Get cached metadata and markdown text for a paper."""
        paper = service_get_cached(cache, doi_or_key, full_text=full_text, max_chars=max_chars)
        if not paper:
            return {"found": False, "message": f"Paper '{doi_or_key}' not found in local cache."}
        return {"found": True, "paper": paper}

    @mcp.tool(
        description=(
            "Retrieve recent search queries and their results count from the local database. "
            "USE THIS TOOL to see what research has already been conducted or to re-run queries."
        )
    )
    def get_search_history(
        query: Annotated[
            str | None,
            Field(description="Optional keyword to filter past search queries."),
        ] = None,
        limit: Annotated[
            int,
            Field(
                description="Maximum number of historical queries to return (default 20).",
                ge=1,
                le=100,
            ),
        ] = 20,
    ) -> dict:
        """List historical search queries indexed in the database."""
        history = service_history(cache, query=query, limit=limit)
        return {"count": len(history), "queries": history}

    @mcp.tool(
        description=(
            "Manually add a paper record with metadata, abstract, notes, or research results "
            "directly into the local SQLite database and FTS5 search index. "
            "USE THIS TOOL to manually catalog papers, add preprints, or record papers "
            "that were not retrieved through automated search."
        )
    )
    def add_paper(
        title: Annotated[str, Field(description="Title of the paper.")],
        authors: Annotated[
            list[str] | str | None,
            Field(
                description="List of author names or comma/and-separated string of authors.",
            ),
        ] = None,
        doi: Annotated[
            str | None,
            Field(description="Digital Object Identifier (DOI) of the paper, if any."),
        ] = None,
        url: Annotated[
            str | None,
            Field(description="Link to the paper or landing page."),
        ] = None,
        publication: Annotated[
            str | None,
            Field(description="Journal, conference, or publisher name."),
        ] = None,
        year: Annotated[
            int | None,
            Field(description="Publication year."),
        ] = None,
        abstract: Annotated[
            str | None,
            Field(description="Abstract of the paper."),
        ] = None,
        research_results: Annotated[
            str | None,
            Field(description="Summary of key findings, methodologies, or research conclusions."),
        ] = None,
        markdown: Annotated[
            str | None,
            Field(description="Optional full text or research notes in Markdown."),
        ] = None,
    ) -> dict:
        """Manually add a paper to the local library and search index."""
        return service_add_paper(
            cache,
            title=title,
            authors=authors,
            doi=doi,
            url=url,
            publication=publication,
            year=year,
            abstract=abstract,
            research_results=research_results,
            markdown=markdown,
        )

    @mcp.tool(
        description=(
            "Update metadata, research results, abstract, or full-text notes of an existing "
            "paper in the local database and full-text search index. "
            "USE THIS TOOL to correct metadata, attach personal notes, or refine paper summaries."
        )
    )
    def update_paper(
        doi_or_key: Annotated[
            str,
            Field(description="DOI or internal dedup_key of the paper to update."),
        ],
        title: Annotated[
            str | None,
            Field(description="Updated title (optional)."),
        ] = None,
        authors: Annotated[
            list[str] | str | None,
            Field(description="Updated author list or string (optional)."),
        ] = None,
        doi: Annotated[
            str | None,
            Field(description="Updated DOI (optional)."),
        ] = None,
        url: Annotated[
            str | None,
            Field(description="Updated URL (optional)."),
        ] = None,
        publication: Annotated[
            str | None,
            Field(description="Updated publication/journal name (optional)."),
        ] = None,
        year: Annotated[
            int | None,
            Field(description="Updated publication year (optional)."),
        ] = None,
        abstract: Annotated[
            str | None,
            Field(description="Updated abstract (optional)."),
        ] = None,
        research_results: Annotated[
            str | None,
            Field(description="Updated research results or findings summary (optional)."),
        ] = None,
        markdown: Annotated[
            str | None,
            Field(description="Updated full-text Markdown or notes (optional)."),
        ] = None,
    ) -> dict:
        """Update an existing paper's metadata or notes in the local library."""
        return service_update_paper(
            cache,
            doi_or_key,
            title=title,
            authors=authors,
            doi=doi,
            url=url,
            publication=publication,
            year=year,
            abstract=abstract,
            research_results=research_results,
            markdown=markdown,
        )

    @mcp.tool(
        description=(
            "Delete a paper permanently from the local SQLite cache and FTS5 search index. "
            "USE THIS TOOL to remove irrelevant papers, clean duplicates, or prune your library."
        )
    )
    def delete_paper(
        doi_or_key: Annotated[
            str,
            Field(description="DOI or key of the paper to delete."),
        ],
    ) -> dict:
        """Delete a paper from the local library."""
        return service_delete_paper(cache, doi_or_key)

    @mcp.tool(
        description=(
            "Format a paper's citation in standard academic styles (APA 7th, Chicago 18th "
            "Author-Date, Chicago Notes & Bibliography, IEEE, MLA 9th, Harvard, or BibTeX). "
            "Returns both the bibliographic reference entry and in-text parenthetical/narrative "
            "citation. USE THIS TOOL whenever you need to cite a paper or generate references."
        )
    )
    def format_citation(
        doi_or_key: Annotated[
            str,
            Field(description="DOI or key of the paper in the local library."),
        ],
        style: Annotated[
            str,
            Field(
                description=(
                    "Citation style: 'apa7' (default), 'chicago' (Author-Date), 'chicago_notes', "
                    "'ieee', 'mla', 'harvard', or 'bibtex'."
                ),
            ),
        ] = "apa7",
        narrative: Annotated[
            bool,
            Field(
                description=(
                    "If True, formats in-text citation in narrative style (e.g. 'Smith (2020)' "
                    "instead of '(Smith, 2020)')."
                ),
            ),
        ] = False,
    ) -> dict:
        """Format a paper's reference and in-text citation in standard styles."""
        return service_format_citation(cache, doi_or_key, style=style, narrative=narrative)

    @mcp.tool(
        description=(
            "Export an academic paper to a standardized LaTeX source file (.tex) and compile "
            "it to a professional PDF document. Supports standardized templates ('academic', "
            "'review', 'brief') or a custom LaTeX template string so that every generated "
            "PDF follows the exact same professional format. "
            "USE THIS TOOL to produce publication-ready PDF reports or literature dossiers."
        )
    )
    def export_paper(
        doi_or_key: Annotated[
            str,
            Field(description="DOI or key of the paper to export."),
        ],
        template: Annotated[
            str,
            Field(
                description=(
                    "Template format: 'academic' (standard paper layout), "
                    "'review' (literature review/dossier with findings box), "
                    "or 'brief' (executive research brief)."
                ),
            ),
        ] = "academic",
        style: Annotated[
            str,
            Field(
                description="Citation style for the references section ('apa7', 'chicago', etc.).",
            ),
        ] = "apa7",
        compile_pdf: Annotated[
            bool,
            Field(
                description=(
                    "If True (default), compiles the LaTeX code to PDF using system compiler."
                ),
            ),
        ] = True,
        output_dir: Annotated[
            str | None,
            Field(description="Optional custom directory path to save the .tex and .pdf files."),
        ] = None,
        custom_template: Annotated[
            str | None,
            Field(
                description=(
                    "Optional raw LaTeX template string with {{title}}, {{authors}}, "
                    "{{publication}}, {{year}}, {{doi}}, {{abstract}}, {{findings}}, "
                    "{{body}}, {{reference}} placeholders."
                ),
            ),
        ] = None,
    ) -> dict:
        """Export a paper into standardized LaTeX source and compile to PDF."""
        return service_export_paper(
            cache,
            doi_or_key,
            template=template,
            style=style,
            compile_pdf=compile_pdf,
            output_dir=output_dir,
            custom_template=custom_template,
        )

    @mcp.tool(
        description=(
            "Export cached papers to a BibTeX (.bib) file or formatted bibliography list "
            "(APA 7th, Chicago, IEEE, MLA, Harvard). "
            "Can export specific papers by keys or the entire local library. "
            "USE THIS TOOL to export citations for LaTeX/Overleaf or to generate reference lists."
        )
    )
    def export_bibliography(
        keys: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional list of DOIs/keys to export. If omitted, exports all cached papers."
                ),
            ),
        ] = None,
        format_type: Annotated[
            str,
            Field(
                description="Output format: 'bibtex' (default), 'text', or 'markdown'.",
            ),
        ] = "bibtex",
        style: Annotated[
            str,
            Field(
                description=(
                    "Citation style when format_type is 'text'/'markdown' "
                    "('apa7', 'chicago', 'ieee', etc.)."
                ),
            ),
        ] = "apa7",
        output_path: Annotated[
            str | None,
            Field(
                description="Optional file path to save bibliography (e.g. '/path/to/refs.bib').",
            ),
        ] = None,
    ) -> dict:
        """Export papers to BibTeX or formatted reference bibliography."""
        return service_export_bib(
            cache,
            keys=keys,
            format_type=format_type,
            style=style,
            output_path=output_path,
        )

    @mcp.tool(
        description=(
            "Scan a Markdown, text, or LaTeX file for citations (Pandoc [@key], inline @key, "
            "HTML comment cite:key, or explicit DOIs). Resolves each against the local database "
            "and reports resolved papers and unresolved citekeys."
        )
    )
    def scan_citations(
        file_path: Annotated[
            str,
            Field(description="Path to Markdown, text, or LaTeX file to scan for citations."),
        ],
    ) -> dict:
        """Scan a document file for citations and resolve against the local library."""
        return service_scan_citations(cache, file_path=file_path)

    @mcp.tool(
        description=(
            "Insert an in-text citation marker or rendered citation into a document file. "
            "Can target a specific line number or append before the bibliography section. "
            "By default automatically updates the document's bibliography section."
        )
    )
    def insert_citation(
        file_path: Annotated[
            str,
            Field(description="Path to the document file (e.g. paper.md, draft.tex)."),
        ],
        doi_or_key: Annotated[
            str,
            Field(description="DOI, dedup_key, or citation key of the paper in cache."),
        ],
        line_number: Annotated[
            int | None,
            Field(
                description=(
                    "Optional line number (1-based) to insert citation into. "
                    "If omitted or beyond body, appends before references section."
                )
            ),
        ] = None,
        marker_format: Annotated[
            str,
            Field(
                description=(
                    "Citation token format: 'pandoc' (e.g. [@author2024]), "
                    "'rendered' (e.g. (Author, 2024)), or 'comment' (<!-- cite: key -->)."
                )
            ),
        ] = "pandoc",
        style: Annotated[
            str,
            Field(
                description=(
                    "Citation style ('apa7', 'chicago', 'ieee', 'mla9', 'harvard') "
                    "used for rendering or bibliography auto-sync."
                )
            ),
        ] = "apa7",
        narrative: Annotated[
            bool,
            Field(
                description=(
                    "Whether to use narrative citation format (e.g. @key or 'Author (2024)')."
                )
            ),
        ] = False,
        auto_sync: Annotated[
            bool,
            Field(description="Automatically regenerate and sync the file's bibliography section."),
        ] = True,
    ) -> dict:
        """Insert a citation marker into a file and optionally sync bibliography."""
        return service_insert_citation(
            cache,
            file_path=file_path,
            doi_or_key=doi_or_key,
            line_number=line_number,
            marker_format=marker_format,
            style=style,
            narrative=narrative,
            auto_sync=auto_sync,
        )

    @mcp.tool(
        description=(
            "Remove all citation tokens matching a paper from a document file. "
            "Removes Pandoc markers, comments, and rendered citations, then automatically "
            "regenerates the bibliography section to keep it in sync."
        )
    )
    def remove_citation(
        file_path: Annotated[
            str,
            Field(description="Path to the document file."),
        ],
        doi_or_key: Annotated[
            str,
            Field(description="DOI, dedup_key, or citekey of the paper to remove."),
        ],
        sync_bib: Annotated[
            bool,
            Field(description="Whether to automatically re-sync the bibliography section."),
        ] = True,
        style: Annotated[
            str,
            Field(description="Citation style to use when re-syncing bibliography."),
        ] = "apa7",
    ) -> dict:
        """Remove paper citations from a document and update bibliography."""
        return service_remove_citation(
            cache,
            file_path=file_path,
            doi_or_key=doi_or_key,
            sync_bib=sync_bib,
            style=style,
        )

    @mcp.tool(
        description=(
            "Scan all citations in a document and automatically generate or update its "
            "formatted references section (e.g. ## References) at the bottom. Also generates "
            "a companion BibTeX file (<file>.bib) for LaTeX/Pandoc integration."
        )
    )
    def sync_bibliography(
        file_path: Annotated[
            str,
            Field(description="Path to the document file (e.g. paper.md)."),
        ],
        style: Annotated[
            str,
            Field(
                description=(
                    "Citation style ('apa7', 'chicago', 'chicago-note', 'ieee', 'mla9', 'harvard')."
                )
            ),
        ] = "apa7",
        section_heading: Annotated[
            str,
            Field(description="Heading markdown for references section (default '## References')."),
        ] = "## References",
        companion_bib: Annotated[
            bool,
            Field(
                description="Whether to generate/update a companion .bib file with BibTeX entries."
            ),
        ] = True,
    ) -> dict:
        """Scan citations in document and sync formatted references and companion .bib."""
        return service_sync_bibliography(
            cache,
            file_path=file_path,
            style=style,
            section_heading=section_heading,
            companion_bib=companion_bib,
        )

    # Register MCP Resources
    @mcp.resource("paper://{+key}", description="Full metadata and cached markdown for a paper")
    def paper_resource(key: str) -> str:
        paper = service_get_cached(cache, key)
        if not paper:
            return f"Paper '{key}' not found in cache."
        return json.dumps(paper, ensure_ascii=False, indent=2)

    @mcp.resource("queries://recent", description="List recent search queries from SQLite")
    def recent_queries_resource() -> str:
        queries = service_history(cache, limit=20)
        return json.dumps(queries, ensure_ascii=False, indent=2)

    # Register MCP Prompts
    @mcp.prompt(
        "literature_review",
        description="Prompt template to guide an agent through a comprehensive literature review",
    )
    def prompt_literature_review(topic: str) -> str:
        return (
            f"You are conducting a rigorous literature review on the topic: '{topic}'.\n"
            "Steps to follow:\n"
            "1. Formulate 2-3 specific search queries covering core concepts and applications.\n"
            "2. Call `search_papers` with these queries across multiple indexes "
            "(including arXiv, CrossRef, and Garuda if Indonesian context is relevant).\n"
            "3. Select the most relevant 3-5 papers and call `download_paper` on their DOIs/URLs "
            "to analyze their methodologies and conclusions.\n"
            "4. Synthesize a comprehensive literature review with structured sections: "
            "Background, Current Methodologies, Key Findings, Research Gaps, and References."
        )

    @mcp.prompt(
        "summarize_paper",
        description="Prompt template to summarize an academic paper in detail",
    )
    def prompt_summarize_paper(doi_or_title: str) -> str:
        return (
            f"Please summarize the academic paper: '{doi_or_title}'.\n"
            "If the paper's full text is not already in context, first look up or download it "
            "using `download_paper`.\n"
            "Then provide a structured summary including:\n"
            "- Objective & Research Question\n"
            "- Methodology & Dataset\n"
            "- Key Results & Contributions\n"
            "- Limitations & Future Work"
        )

    @mcp.tool(
        description=(
            "Perform a system health check: verifies database connectivity, FTS5 index, "
            "and supported index availability."
        )
    )
    def health_check() -> dict:
        """Verify database and adapter health."""
        cached_count = len(cache.list_all(limit=1))
        return {
            "status": "healthy",
            "database": "ok",
            "db_path": str(cache.path),
            "cached_papers_available": cached_count >= 0,
            "supported_sources": registry.supported_sources(),
        }

    return mcp


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="artikel-mcp academic research MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse"],
        help="MCP transport protocol (stdio or sse)",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE transport")
    args = parser.parse_args()

    server = create_server()
    if args.transport == "sse":
        logger.info("starting artikel-mcp on sse port %d", args.port)
        server.run(transport="sse", port=args.port)
    else:
        logger.info("starting artikel-mcp on stdio")
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
