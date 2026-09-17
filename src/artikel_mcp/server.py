import json
import logging
from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

from artikel_mcp.cache import PaperCache
from artikel_mcp.logging import setup_logging
from artikel_mcp.service import download_paper as service_download
from artikel_mcp.service import get_cached_paper as service_get_cached
from artikel_mcp.service import get_search_history as service_history
from artikel_mcp.service import ingest_bibliography as service_ingest
from artikel_mcp.service import search_papers as service_search

logger = logging.getLogger("artikel_mcp")

SERVER_INSTRUCTIONS = """You are connected to artikel-mcp, an academic research server.

CRITICAL INSTRUCTIONS FOR USING TOOLS:
1. STRICT ANTI-BASH, ANTI-RG, & ANTI-WEBFETCH DIRECTIVE:
   - NEVER execute bash commands (grep, rg, ripgrep, find, curl, sqlite3) OR generic web tools
     (WebFetch, Exa) to retrieve academic papers, parse journal pages, or inspect tool output.
   - NEVER run `rg` or `grep` on tool output files (e.g. `/home/rd/.../tool-output/...`).
     If you need specific papers, refine your keywords directly with `search_papers`!
   - Generic WebFetch/curl tools lack browser impersonation and get blocked by HTTP 403!
   - All scholarly literature workflows MUST use the `search_papers`, `download_paper`,
     `get_cached_paper`, and `get_search_history` tools.

2. MANDATORY 5-PART PRESENTATION FORMAT:
   - When presenting search results to the user, you MUST display for EVERY paper:
     1. **Title**
     2. **Authors**
     3. **Publication** (Journal / Conference / Venue, Year)
     4. **DOI / Link** (Clickable URL)
     5. **Research Results & Key Findings** (Extracted findings from abstract)
   - You can directly output the pre-rendered `formatted` field of each record or the
     `formatted_summary` payload. Never omit any of these 5 fields, even for 50 or 100 papers!

3. ALWAYS USE `search_papers` WHEN:
   - The user asks to find, search, explore, or retrieve academic papers, scientific literature,
     journal articles, or conference proceedings.
   - The user asks for Indonesian academic literature or legal journals (uses the 'garuda' source
     connecting directly to Kemdiktisaintek Garuda).
    - The user asks about computer science/physics/math preprints (arXiv), open-access publications
      (DOAJ), global research catalogs (OpenAlex), biomedical/life-sciences
      (PubMed / Europe PMC / PMC), AI-backed citation graphs (Semantic Scholar),
      or cross-publisher DOIs (Crossref).
    - You can request specific quantities directly (e.g. 'cari 50 artikel tentang x', limit=50).

4. ALWAYS USE `download_paper` WHEN:
    - The user asks to read, analyze, explain, or extract the full text/markdown of a specific
      paper where a DOI, URL (article landing page, OJS, DOAJ, Garuda), or PDF URL is known.
    - `download_paper` has a built-in Open Journal Systems (OJS) & academic repository engine:
      it automatically parses OJS article pages (`/article/view/...`), resolves DOI landing pages,
      discovers OpenAlex & Unpaywall open-access copies, locates PDF galleys, bypasses 403 blocks
      with browser impersonation, and extracts Markdown.

5. USE `get_cached_paper` WHEN:
   - You need to re-read or inspect full text/markdown of a paper previously searched or cached.

6. USE `get_search_history` WHEN:
   - The user asks what topics or queries have been researched previously, or to review past
     search results.
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
            "DOAJ, EuropePMC, HAL, PMC, OpenAlex, PubMed, Semantic Scholar) and local FTS cache. "
            "Automatically persists all results and indexes queries into SQLite. Returns "
            "guaranteed title, authors, publication, DOI/link, and research results. "
            "USE THIS TOOL whenever the user asks for academic papers, journals, "
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
        sources: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional list of indexes to query: 'arxiv', 'crossref', 'garuda' "
                    "(Indonesian portal), 'doaj', 'europepmc', 'hal', 'pmc', 'openalex', "
                    "'pubmed', 'semantic', or 'local' (local database only). If omitted, "
                    "searches local cache first and fans out to all upstream sources on cache miss."
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
    ) -> dict:
        """Search academic indexes and return normalized paper records."""
        return service_search(
            cache, query, sources=sources, limit=limit, force_refresh=force_refresh
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
    ) -> dict:
        """Download a paper's PDF, return extracted markdown, and persist it to SQLite."""
        return service_download(
            cache, doi=doi, url=url, pdf_url=pdf_url, force_fallback=force_fallback
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
    ) -> dict:
        """Get cached metadata and markdown text for a paper."""
        paper = service_get_cached(cache, doi_or_key)
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

    return mcp


def main() -> None:
    server = create_server()
    logger.info("starting artikel-mcp on stdio")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
