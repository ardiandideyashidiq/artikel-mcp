import logging

from mcp.server import MCPServer

from artikel_mcp.logging import setup_logging

logger = logging.getLogger("artikel_mcp")


def create_server() -> MCPServer:
    setup_logging()
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

    return mcp


def main() -> None:
    server = create_server()
    logger.info("starting artikel-mcp on stdio")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
