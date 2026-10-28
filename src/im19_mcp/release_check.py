"""Read-only, redacted HTTPS smoke checks for a *deployed* IM19 MCP gateway.

This is not a certification of production readiness: no real OAuth login,
provider writes, backup/restore, or key-recovery operation is performed.
No tool is exposed over MCP and no bearer token is printed or persisted.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE_BYTES = 131072
MCP_VERSION = "2025-11-25"


class SmokeError(ValueError):
    """Static public-facing reason codes only."""


def _https_url(value: str, *, resource: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise SmokeError("INVALID_HTTPS_ENDPOINT")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.netloc or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.endswith("/") and resource):
        raise SmokeError("INVALID_HTTPS_ENDPOINT")
    if resource and parsed.path != "/mcp":
        raise SmokeError("MCP_RESOURCE_PATH_INVALID")
    if not resource and parsed.path not in ("", "/", parsed.path.rstrip("/")):
        raise SmokeError("INVALID_ISSUER_ENDPOINT")
    return value.rstrip("/")


def _json_response(response: httpx.Response, reason: str) -> dict:
    if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
        raise SmokeError(reason)
    try:
        payload = response.json()
    except ValueError:
        raise SmokeError(reason) from None
    if not isinstance(payload, dict):
        raise SmokeError(reason)
    return payload


def _same_origin(url: str, origin: str) -> bool:
    parsed, expected = urlsplit(url), urlsplit(origin)
    return (parsed.scheme == "https" and parsed.netloc == expected.netloc
            and not parsed.username and not parsed.password)


def check_public(resource: str, issuer: str, client: httpx.Client) -> dict:
    """Non-mutating production smoke: discovery, readiness and denial paths."""
    resource = _https_url(resource, resource=True)
    issuer = _https_url(issuer)
    origin = resource.removesuffix("/mcp")
    metadata = _json_response(
        client.get(origin + "/.well-known/oauth-protected-resource/mcp"),
        "RESOURCE_DISCOVERY_FAILED",
    )
    if (metadata.get("resource") != resource
            or not isinstance(metadata.get("authorization_servers"), list)
            or issuer not in metadata["authorization_servers"]
            or "im19:read" not in metadata.get("scopes_supported", [])):
        raise SmokeError("RESOURCE_METADATA_INVALID")
    provider = _json_response(
        client.get(issuer + "/.well-known/openid-configuration"),
        "ISSUER_DISCOVERY_FAILED",
    )
    if (provider.get("issuer") != issuer
            or not isinstance(provider.get("code_challenge_methods_supported"), list)
            or "S256" not in provider["code_challenge_methods_supported"]
            or not all(isinstance(provider.get(key), str)
                       and _same_origin(provider[key], issuer)
                       for key in ("authorization_endpoint", "token_endpoint"))):
        raise SmokeError("ISSUER_METADATA_INVALID")
    ready = _json_response(client.get(origin + "/ready"), "GATEWAY_NOT_READY")
    if ready.get("status") != "ready":
        raise SmokeError("GATEWAY_NOT_READY")
    denied = client.post(
        resource, headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    if (denied.status_code != 401
            or "resource_metadata=" not in denied.headers.get("www-authenticate", "")):
        raise SmokeError("ANONYMOUS_MCP_CHALLENGE_INVALID")
    foreign_host = client.get(origin + "/health", headers={"Host": "invalid.example"})
    if foreign_host.status_code != 421:
        raise SmokeError("FOREIGN_HOST_NOT_REJECTED")
    foreign_origin = client.get(origin + "/health", headers={"Origin": "https://invalid.example"})
    if foreign_origin.status_code != 403:
        raise SmokeError("FOREIGN_ORIGIN_NOT_REJECTED")
    return {
        "public_discovery_verified": True,
        "issuer_discovery_verified": True,
        "database_readiness_verified": True,
        "anonymous_auth_challenge_verified": True,
        "host_origin_denial_verified": True,
    }


def check_authenticated(resource: str, token: str, client: httpx.Client) -> dict:
    """A test token is supplied only via protected environment, never CLI args."""
    resource = _https_url(resource, resource=True)
    if not isinstance(token, str) or not 10 <= len(token) <= 8192 or any(c.isspace() for c in token):
        raise SmokeError("TEST_TOKEN_REQUIRED")
    headers = {"Authorization": "Bearer " + token,
               "Accept": "application/json, text/event-stream",
               "Mcp-Protocol-Version": MCP_VERSION}

    def rpc(identifier: int, method: str, params: dict, *, protocol: str = MCP_VERSION) -> dict:
        h = dict(headers)
        h["Mcp-Protocol-Version"] = protocol
        response = client.post(resource, headers=h,
                               json={"jsonrpc": "2.0", "id": identifier, "method": method,
                                     "params": params})
        body = _json_response(response, "MCP_" + method.replace("/", "_").upper() + "_FAILED")
        if (body.get("jsonrpc") != "2.0" or body.get("id") != identifier
                or not isinstance(body.get("result"), dict) or "error" in body):
            raise SmokeError("MCP_RESPONSE_INVALID")
        return body["result"]

    handshake = rpc(1, "initialize", {
        "protocolVersion": MCP_VERSION, "capabilities": {},
        "clientInfo": {"name": "im19-production-smoke", "version": "1"},
    })
    version = handshake.get("protocolVersion")
    if not isinstance(version, str) or version != MCP_VERSION or not isinstance(
        handshake.get("capabilities"), dict
    ):
        raise SmokeError("MCP_HANDSHAKE_INVALID")
    notification = client.post(
        resource, headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    if notification.status_code != 202:
        raise SmokeError("MCP_NOTIFICATION_FAILED")
    listing = rpc(2, "tools/list", {})
    tools = listing.get("tools")
    if (not isinstance(tools, list) or not tools
            or any(not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
                   or not isinstance(tool.get("inputSchema"), dict) for tool in tools)
            or "get_connection_status" not in {tool["name"] for tool in tools}):
        raise SmokeError("MCP_TOOL_CONTRACT_INVALID")
    status = rpc(3, "tools/call", {
        "name": "get_connection_status", "arguments": {"service": "drive"},
    })
    if (status.get("isError") or not isinstance(status.get("content"), list)
            or not status["content"]):
        raise SmokeError("MCP_READ_TOOL_FAILED")
    return {
        "authenticated_mcp_handshake_verified": True,
        "authenticated_tool_discovery_verified": True,
        "read_only_mcp_tool_verified": True,
    }


def run_smoke(resource: str, issuer: str, *, token: str = "",
              client: httpx.Client | None = None) -> dict:
    """Return a redacted report. An absent token is NOT an authentication PASS."""
    from contextlib import nullcontext

    # Validate before making any request.
    _https_url(resource, resource=True)
    _https_url(issuer)
    owns_client = client is None
    manager = (httpx.Client(timeout=httpx.Timeout(10, connect=5), trust_env=False,
                            follow_redirects=False)
               if owns_client else nullcontext(client))
    with manager as active:
        try:
            checks = check_public(resource, issuer, active)
            if token:
                checks.update(check_authenticated(resource, token, active))
            return {"status": "SMOKE_PASSED" if token else "PUBLIC_PREFLIGHT_ONLY",
                    "checks": checks, "authenticated_tested": bool(token),
                    "live_google_verified": False, "chatgpt_connected_verified": False,
                    "backup_restore_verified": False, "production_acceptance": False}
        except (SmokeError, httpx.HTTPError, UnicodeError):
            # No backend response, URL, bearer token or exception string leaks.
            return {"status": "SMOKE_FAILED", "checks": {},
                    "authenticated_tested": False,
                    "live_google_verified": False, "chatgpt_connected_verified": False,
                    "backup_restore_verified": False, "production_acceptance": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only redacted IM19 live HTTPS acceptance smoke")
    parser.add_argument("--mcp-resource", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--public-only", action="store_true",
                        help="Check unauthenticated endpoints; never claim MCP auth tested")
    args = parser.parse_args(argv)
    token = "" if args.public_only else os.getenv("IM19_MCP_TEST_TOKEN", "")
    report = run_smoke(args.mcp_resource, args.issuer, token=token)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "SMOKE_PASSED" or (
        args.public_only and report["status"] == "PUBLIC_PREFLIGHT_ONLY"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
