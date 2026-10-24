"""IM19 MCP tool surface. No model-supplied user identifiers or access tokens."""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token

from im19_mcp.config import Settings
from im19_mcp.google_oauth import GoogleOAuth, OAuthError, SCOPES
from im19_mcp.registry import SERVICES, list_services
from im19_mcp.storage import Vault
from im19_mcp.youtube import ProviderError, YouTubeClient


def authenticated_subject(*, allow_stdio_dev: bool = False) -> str | None:
    token = get_access_token()
    if token is not None and token.subject:
        return token.subject
    if allow_stdio_dev:
        return os.getenv("IM19_DEV_SUBJECT") or None
    return None


def register_tools(
    server: MCPServer, *, settings: Settings, vault: Vault | None,
    oauth: GoogleOAuth | None, allow_stdio_dev: bool = False,
    provider_factory: Callable[[str], YouTubeClient] = YouTubeClient,
) -> None:
    """Register tools; provider calls always obtain tokens from backend vault."""
    def subject() -> str | None:
        return authenticated_subject(allow_stdio_dev=allow_stdio_dev)

    def audit(user: str, action: str, status: str) -> None:
        if vault:
            vault.audit(user, "youtube", action, status)

    @server.tool(name="list_available_services")
    def list_available_services() -> dict:
        """List known providers and implemented capabilities, not login status."""
        return {"services": list_services(), "live_connection_claimed": False}

    @server.tool(name="get_service_capabilities")
    def get_service_capabilities(service: str) -> dict:
        """Discover read-only capabilities and scopes of a registered provider."""
        selected = SERVICES.get(service)
        if selected is None:
            return {"status": "UNKNOWN_SERVICE"}
        return {"service": service, "implemented": selected.implemented,
                "tools": list(selected.capabilities), "required_scopes": list(selected.scopes)}

    @server.tool(name="get_connection_status")
    def get_connection_status(service: str) -> dict:
        """Check the authenticated user's stored connection, without exposing tokens."""
        if service not in SERVICES:
            return {"status": "UNKNOWN_SERVICE"}
        user = subject()
        if not user:
            return {"status": "AUTH_REQUIRED", "reason": "verified_mcp_identity_required"}
        if service != "youtube":
            return {"status": "NOT_IMPLEMENTED", "service": service}
        if vault is None:
            return {"status": "NOT_CONFIGURED", "service": service}
        current = vault.get_credential(user, "youtube")
        if current is None:
            return {"status": "AUTH_REQUIRED", "service": service}
        return {"status": "CONNECTED" if current[2] > time.time() else "EXPIRED_OR_REFRESH_REQUIRED",
                "service": service, "account_id": current[0],
                "scopes": list(current[1].get("scopes", []))}

    @server.tool(name="request_service_authorization")
    def request_service_authorization(service: str, include_analytics: bool = False) -> dict:
        """Return official Google OAuth consent URL for authenticated user, or fail closed."""
        user = subject()
        if not user:
            return {"status": "AUTH_REQUIRED", "reason": "verified_mcp_identity_required"}
        if service != "youtube":
            return {"status": "NOT_IMPLEMENTED", "service": service}
        if oauth is None:
            return {"status": "NOT_CONFIGURED", "reason": "google_oauth_credentials_required"}
        try:
            return oauth.begin(user, analytics=include_analytics)
        except (ValueError, PermissionError):
            return {"status": "DENY", "reason": "oauth_request_invalid"}

    @server.tool(name="disconnect_service")
    def disconnect_service(service: str) -> dict:
        """Never revoke an account through an unapproved AI-initiated tool call."""
        if service not in SERVICES:
            return {"status": "UNKNOWN_SERVICE"}
        return {"status": "APPROVAL_REQUIRED", "reason": "disconnect_in_trusted_account_portal"}

    async def execute(action: str, required: set[str], operation) -> dict:
        user = subject()
        if not user:
            return {"status": "AUTH_REQUIRED", "reason": "verified_mcp_identity_required"}
        if oauth is None:
            return {"status": "NOT_CONFIGURED", "reason": "google_oauth_credentials_required"}
        try:
            account_id, access = await oauth.access_token(user, required)
            client = provider_factory(access)
            data = await operation(client)
            audit(user, action, "SUCCESS")
            return {"status": "OK", "service": "youtube", "account_id": account_id, "data": data}
        except OAuthError as exc:
            audit(user, action, "DENY")
            return {"status": str(exc)}
        except (ProviderError, ValueError) as exc:
            audit(user, action, "ERROR")
            return {"status": "ERROR", "error_code": str(exc)}
        except Exception:
            audit(user, action, "ERROR")
            return {"status": "ERROR", "error_code": "UNEXPECTED_BACKEND_ERROR"}

    @server.tool(name="youtube_get_channel")
    async def youtube_get_channel() -> dict:
        """Read the connected user's YouTube channel profile and statistics."""
        return await execute("youtube_get_channel", set(SCOPES["youtube"]), lambda c: c.channel())

    @server.tool(name="youtube_list_videos")
    async def youtube_list_videos(max_results: int = 25) -> dict:
        """Read up to 50 items from the user's uploads playlist; no publishing."""
        if not 1 <= max_results <= 50:
            return {"status": "INVALID_ARGUMENT", "reason": "max_results_must_be_1_to_50"}
        return await execute("youtube_list_videos", set(SCOPES["youtube"]),
                             lambda c: c.videos(max_results))

    @server.tool(name="youtube_get_analytics")
    async def youtube_get_analytics(start_date: str, end_date: str) -> dict:
        """Read daily channel Analytics for ISO start/end dates up to 366 days apart."""
        return await execute("youtube_get_analytics", set(SCOPES["youtube_analytics"]),
                             lambda c: c.analytics(start_date, end_date))


def create_stdio_server(settings: Settings | None = None) -> MCPServer:
    cfg = settings or Settings.from_env()
    vault = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt) if cfg.vault_key else None
    if vault:
        vault.initialize_schema()
    oauth = GoogleOAuth(cfg, vault) if vault and cfg.google_enabled() else None
    server = MCPServer("IM19 Universal MCP")
    register_tools(server, settings=cfg, vault=vault, oauth=oauth, allow_stdio_dev=True)
    return server


def main() -> None:
    """Local stdio only. Remote deployment MUST use im19-gateway."""
    create_stdio_server().run(transport="stdio")


if __name__ == "__main__":
    main()
