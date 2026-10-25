"""Smoke test a *real stdio subprocess* using the official MCP Python SDK.

Does not use the developer's Google, GitHub, OpenAI or IM19 credentials.
Does not initialize a database or expose a public HTTP port.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "codespaces" / "launch_stdio.sh"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"

REQUIRED_TOOLS = {
    "list_available_services",
    "get_service_capabilities",
    "get_connection_status",
    "drive_search_files",
    "youtube_get_channel",
}


def clean_process_environment() -> dict[str, str]:
    """Explicitly do not forward ambient user secrets or IM19_* settings."""
    allowed = (
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
        "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "LD_LIBRARY_PATH",
    )
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result["PYTHONPATH"] = str(ROOT / "src")
    result["IM19_DEPLOYMENT_ENV"] = "development"
    return result


def parse_tool_result(result) -> dict:
    if result.is_error:
        raise RuntimeError("mcp_tool_returned_error")
    if result.structured_content is not None:
        return result.structured_content
    if not result.content or not hasattr(result.content[0], "text"):
        raise RuntimeError("mcp_tool_has_no_text_payload")
    return json.loads(result.content[0].text)


async def probe_stdio() -> dict[str, object]:
    if VENV_PYTHON.is_file():
        # Test the SAME safe launcher that Codex will use in Codespaces.
        params = StdioServerParameters(
            command="bash",
            args=[str(LAUNCHER)],
            env=clean_process_environment(),
        )
        mode = "codespaces_venv_launcher"
    else:
        # CI installs the project into its runner, without creating .venv.
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "im19_mcp.server"],
            env=clean_process_environment(),
        )
        mode = "installed_package_launcher"

    async with Client(params) as client:
        listed = await client.list_tools()
        names = {item.name for item in listed.tools}
        if not REQUIRED_TOOLS.issubset(names):
            raise RuntimeError("mcp_required_tools_missing")

        resources = await client.list_resources()
        if "im19://capabilities" not in {str(r.uri) for r in resources.resources}:
            raise RuntimeError("mcp_capabilities_resource_missing")

        services = parse_tool_result(
            await client.call_tool("list_available_services", {})
        )
        status = parse_tool_result(
            await client.call_tool("get_connection_status", {"service": "drive"})
        )
        if services.get("live_connection_claimed") is not False:
            raise RuntimeError("mcp_invalid_live_connection_claim")
        if status.get("status") != "AUTH_REQUIRED":
            raise RuntimeError("mcp_dev_probe_must_not_connect_google")

        return {
            "result": "IM19_CODESPACES_MCP_PASS",
            "transport": "stdio",
            "launcher": mode,
            "tool_count": len(names),
            "drive_status": status["status"],
            "google_live_verified": False,
            "public_http_endpoint": False,
        }


if __name__ == "__main__":
    try:
        result = asyncio.run(asyncio.wait_for(probe_stdio(), timeout=30))
    except (RuntimeError, OSError, TimeoutError) as exc:
        print(f"IM19_CODESPACES_MCP_FAIL: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, sort_keys=True))
