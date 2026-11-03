import asyncio
import json
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

READ_ONLY_TOOLS = {
    "list_available_services",
    "get_service_capabilities",
    "get_connection_status",
    "youtube_get_channel",
    "youtube_list_videos",
    "youtube_get_analytics",
    "drive_search_files",
    "drive_list_folder",
    "drive_get_metadata",
    "cloudbrowser_get_agent_card",
    "cloudbrowser_get_task_summary",
    "cloudbrowser_list_task_summaries",
    "cloudbrowser_get_readiness",
}


@pytest.mark.asyncio
async def test_in_process_mcp_tool_discovery():
    async with Client(create_stdio_server()) as client:
        listed_tools = (await client.list_tools()).tools
        names = {tool.name for tool in listed_tools}
        assert REQUIRED.issubset(names)
        by_name = {tool.name: tool for tool in listed_tools}
        for name in READ_ONLY_TOOLS:
            annotations = by_name[name].annotations
            assert annotations is not None
            if hasattr(annotations, "model_dump"):
                annotations = annotations.model_dump(by_alias=True, exclude_none=True)
            read_only = annotations.get(
                "readOnlyHint", annotations.get("read_only_hint")
            ) if isinstance(annotations, dict) else getattr(
                annotations, "read_only_hint", getattr(annotations, "readOnlyHint", None)
            )
            assert read_only is True, name
        assert any(str(resource.uri) == "im19://capabilities"
                   for resource in (await client.list_resources()).resources)
        assert any(prompt.name == "im19_security_review"
                   for prompt in (await client.list_prompts()).prompts)
        result = await client.call_tool("list_available_services", {})
        assert result.is_error is False
        payload = result.structured_content
        if payload is None:
            payload = json.loads(result.content[0].text)
        assert payload["live_connection_claimed"] is False


@pytest.mark.asyncio
async def test_stdio_tool_discovery():
    params = StdioServerParameters(command=sys.executable, args=["-m", "im19_mcp.server"])
    async def probe():
        async with Client(params) as client:
            assert REQUIRED.issubset({tool.name for tool in (await client.list_tools()).tools})
    await asyncio.wait_for(probe(), timeout=15)
