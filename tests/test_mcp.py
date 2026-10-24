import asyncio
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from im19_mcp.server import create_stdio_server


REQUIRED = {
    "list_available_services", "get_service_capabilities", "get_connection_status",
    "request_service_authorization", "disconnect_service", "youtube_get_channel",
    "youtube_list_videos", "youtube_get_analytics",
}


@pytest.mark.asyncio
async def test_in_process_mcp_tool_discovery():
    async with Client(create_stdio_server()) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert REQUIRED.issubset(names)
        result = await client.call_tool("list_available_services", {})
        assert result.is_error is False
        assert result.structured_content["live_connection_claimed"] is False


@pytest.mark.asyncio
async def test_stdio_tool_discovery():
    params = StdioServerParameters(command=sys.executable, args=["-m", "im19_mcp.server"])
    async def probe():
        async with Client(params) as client:
            assert REQUIRED.issubset({tool.name for tool in (await client.list_tools()).tools})
    await asyncio.wait_for(probe(), timeout=15)
