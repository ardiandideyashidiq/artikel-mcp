import logging

from mcp.server import MCPServer

from artikel_mcp.cache import PaperCache
from artikel_mcp.logging import setup_logging
from artikel_mcp.service import download_paper as service_download
from artikel_mcp.service import search_papers as service_search

logger = logging.getLogger("artikel_mcp")


def create_server(db_path: str | None = None) -> MCPServer:
    setup_logging()
    cache = PaperCache(db_path)
    mcp = MCPServer(
        "artikel-mcp",
        title="Artikel MCP Server",
        description="MCP server for article workflows.",
    )

    @mcp.tool()
    def ping() -> str:
        """Health check that always returns pong."""
        logger.debug("ping called")
        return "pong"

    @mcp.tool()
    def word_count(text: str) -> int:
        """Count words in the given text."""
        count = len(text.split())
        logger.info("word_count computed %d words", count)
        return count

    @mcp.tool()
    def search_papers(query: str, sources: list[str] | None = None) -> dict:
        """Search academic indexes and return normalized paper records.

        Cache-first: matches the local FTS index (created from every prior
        fetch) are returned without touching upstream. On a local miss, the
        requested upstream sources are queried and their results persisted.
        Supported sources: crossref, arxiv, doaj, europepmc, hal, pmc, garuda.
        Pass "local" alone (or omit sources) to search only the local cache.
        """
        return service_search(cache, query, sources)

    @mcp.tool()
    def download_paper(doi: str | None = None, pdf_url: str | None = None) -> dict:
        """Download a paper's PDF and return extracted markdown.

        Provide a DOI and/or direct pdf_url. Direct fetch uses a
        browser-impersonating client; if it fails (paywall/403/non-PDF),
        resolves an open-access copy via Unpaywall (needs UNPAYWALL_EMAIL).
        """
        return service_download(cache, doi=doi, pdf_url=pdf_url)

    return mcp


def main() -> None:
    server = create_server()
    logger.info("starting artikel-mcp on stdio")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
