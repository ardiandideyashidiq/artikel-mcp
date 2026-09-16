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
from artikel_mcp.service import search_papers as service_search

logger = logging.getLogger("artikel_mcp")

SERVER_INSTRUCTIONS = """You are connected to artikel-mcp, an academic research server.

CRITICAL INSTRUCTIONS FOR USING TOOLS:
1. ALWAYS USE `search_papers` WHEN:
   - The user asks to find, search, explore, or retrieve academic papers, scientific literature,
     journal articles, or conference proceedings.
   - The user asks for Indonesian academic literature or legal journals (uses the 'garuda' source
     connecting directly to Kemdiktisaintek Garuda).
   - The user asks about computer science/physics/math preprints (arXiv), open-access publications
     (DOAJ), biomedical/life-sciences (Europe PMC / PMC), or cross-publisher DOIs (Crossref).
   - NEVER hallucinate citations or pretend you searched academic indexes.
     Always execute `search_papers` first!

2. ALWAYS USE `download_paper` WHEN:
   - The user asks to read, analyze, explain, or extract the full text/markdown of a specific
     paper where a DOI or PDF URL is known.
   - The tool automatically bypasses paywalls for open-access papers via Unpaywall and converts
     PDFs into clean, structured Markdown.

3. USE `get_cached_paper` WHEN:
   - You need to re-read or inspect full text/markdown of a paper that was previously searched
     or downloaded in this session.

4. USE `get_search_history` WHEN:
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
            "DOAJ, EuropePMC, HAL, PMC) and local FTS cache. Automatically persists all results "
            "and indexes queries into SQLite. USE THIS TOOL whenever the user asks for "
            "academic papers, journals, literature reviews, or research on any topic."
        )
    )
    def search_papers(
        query: Annotated[
            str,
            Field(
                description=(
                    "The research topic, question, title, keywords, DOI (e.g. '10.1038/...'), "
                    "or arXiv ID to search for. Conversational phrasing ('tolong cari...', "
                    "'find papers about...') is automatically cleaned."
                )
            ),
        ],
        sources: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional list of indexes to query: 'arxiv', 'crossref', 'garuda' "
                    "(Indonesian portal), 'doaj', 'europepmc', 'hal', 'pmc', or 'local' "
                    "(local database only). If omitted, searches local cache first and fans out "
                    "to all upstream sources on cache miss."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                description="Maximum number of papers to return (default 20, max 50).",
                ge=1,
                le=50,
            ),
        ] = 20,
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
            "Automatically resolves open-access copies via Unpaywall if direct fetch hits "
            "a paywall or 403. Saves the extracted Markdown permanently into local SQLite for "
            "instant re-reading and full-text search. USE THIS TOOL whenever you need to read "
            "or analyze the full content of a paper with a DOI or PDF URL."
        )
    )
    def download_paper(
        doi: Annotated[
            str | None,
            Field(
                description=(
                    "Digital Object Identifier of the paper (e.g. '10.1038/s41586-020-2649-2')."
                )
            ),
        ] = None,
        pdf_url: Annotated[
            str | None,
            Field(description="Direct URL to the paper's PDF file."),
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
        return service_download(cache, doi=doi, pdf_url=pdf_url, force_fallback=force_fallback)

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
