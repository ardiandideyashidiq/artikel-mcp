"""End-to-end MCP protocol tests over real stdio transport.

These spawn the actual `artikel-mcp` server as a subprocess and drive it with
a genuine MCP client (`mcp.client.stdio`), so they exercise the JSON-RPC wire
format the in-process `srv.call_tool` tests never see: initialize handshake,
tools/list, tools/call, resources, and prompts.

Everything here is marked `network` because one case performs a live
CrossRef search; run with `uv run pytest -m network`.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from test_tool_smoke import EXPECTED_TOOLS

pytestmark = pytest.mark.network

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _server_params(db_path: Path):
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=os.path.join(sys.prefix, "bin", "artikel-mcp"),
        args=[],
        env={**os.environ, "ARTIKEL_MCP_DB": str(db_path)},
        cwd=str(PROJECT_ROOT),
    )


def test_protocol_listing_and_local_calls(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async def main():
        sessions = stdio_client(_server_params(tmp_path / "proto.db"))
        async with sessions as (read, write), ClientSession(read, write) as session:
            await session.initialize()

            # tools/list
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == EXPECTED_TOOLS

            # tools/call: local, offline-safe
            health = await session.call_tool("health_check", {})
            assert health.is_error is False
            assert "healthy" in health.content[0].text

            search = await session.call_tool(
                "search_papers", {"query": "xyzzy-not-cached", "sources": ["local"]}
            )
            assert search.is_error is False
            data = json.loads(search.content[0].text)
            assert data["from_local"] is True

            # resources/list + templates + prompts
            resources = await session.list_resources()
            assert {r.uri for r in resources.resources} == {"queries://recent"}
            templates = await session.list_resource_templates()
            assert {t.uri_template for t in templates.resource_templates} == {"paper://{+key}"}
            prompts = await session.list_prompts()
            assert {p.name for p in prompts.prompts} == {"literature_review", "summarize_paper"}

            # prompts/get
            got = await session.get_prompt("literature_review", {"topic": "deepfake detection"})
            assert got.messages

            # resources/read
            recent = await session.read_resource("queries://recent")
            assert recent.contents

    asyncio.run(main())


def test_protocol_live_search(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async def main():
        sessions = stdio_client(_server_params(tmp_path / "live.db"))
        async with sessions as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_papers",
                {"query": "deep learning", "source": "crossref", "limit": 3},
                read_timeout_seconds=120,
            )
            assert result.is_error is False
            data = json.loads(result.content[0].text)
            assert data["count"] >= 1
            assert data["sources_queried"] == ["crossref"]

    asyncio.run(main())


def test_protocol_unknown_tool_returns_error(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async def main():
        sessions = stdio_client(_server_params(tmp_path / "badtool.db"))
        async with sessions as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("no_such_tool", {})
            assert result.is_error is True
            assert "no_such_tool" in result.content[0].text

    asyncio.run(main())
