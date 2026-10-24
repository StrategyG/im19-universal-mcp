"""Authenticated Streamable HTTP resource server for IM19.

ChatGPT -> MCP OAuth is distinct from Google OAuth. All /mcp requests
require opaque bearer token introspection from a trusted authorization server.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

import httpx
import uvicorn
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse

from im19_mcp.config import Settings
from im19_mcp.google_oauth import GoogleOAuth, OAuthError
from im19_mcp.server import register_tools
from im19_mcp.storage import Vault

logger = logging.getLogger(__name__)


class IntrospectionVerifier(TokenVerifier):
    def __init__(self, cfg: Settings):
        self.cfg = cfg

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.post(
                    self.cfg.mcp_introspection,
                    data={"token": token},
                    auth=(self.cfg.mcp_client_id, self.cfg.mcp_client_secret),
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("mcp_token_introspection_failed")
            return None
        if not isinstance(body, dict) or body.get("active") is not True:
            return None
        if body.get("iss") != self.cfg.mcp_issuer:
            return None
        audience = body.get("aud")
        if not (audience == self.cfg.mcp_resource or
                isinstance(audience, list) and self.cfg.mcp_resource in audience):
            return None
        expiry = body.get("exp")
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or expiry <= time.time():
            return None
        if not isinstance(body.get("client_id"), str) or not body["client_id"]:
            return None
        if not isinstance(body.get("sub"), str) or not body["sub"]:
            return None
        scope = body.get("scope")
        scopes = scope.split() if isinstance(scope, str) else []
        if "im19:read" not in scopes:
            return None
        return AccessToken(
            token=token, client_id=body["client_id"], subject=body["sub"],
            resource=self.cfg.mcp_resource, scopes=scopes,
        )


def create_gateway(settings: Settings | None = None) -> MCPServer:
    cfg = settings or Settings.from_env()
    cfg.require_remote()
    vault = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt)
    # Database schema must be created through an explicit migration/bootstrap step.
    oauth = GoogleOAuth(cfg, vault) if cfg.google_enabled() else None
    server = MCPServer(
        "IM19 Universal MCP",
        token_verifier=IntrospectionVerifier(cfg),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(cfg.mcp_issuer),
            resource_server_url=AnyHttpUrl(cfg.mcp_resource),
            required_scopes=["im19:read"],
            validate_token_resource=True,
        ),
    )
    register_tools(server, settings=cfg, vault=vault, oauth=oauth)

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "im19-universal-mcp"},
                            headers={"Cache-Control": "no-store"})

    @server.custom_route("/oauth/google/callback", methods=["GET"])
    async def google_callback(request: Request) -> JSONResponse:
        if oauth is None:
            return JSONResponse({"status": "NOT_CONFIGURED"}, status_code=503)
        state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
        if request.query_params.get("error"):
            return JSONResponse({"status": "DENIED_BY_PROVIDER"}, status_code=400,
                                headers={"Cache-Control": "no-store"})
        try:
            response = await oauth.complete(state=state, code=code)
            return JSONResponse(response, headers={"Cache-Control": "no-store",
                                                  "Referrer-Policy": "no-referrer"})
        except (OAuthError, PermissionError, ValueError):
            return JSONResponse({"status": "OAUTH_FAILED"}, status_code=400,
                                headers={"Cache-Control": "no-store"})

    @server.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
    async def metadata(_: Request) -> JSONResponse:
        return JSONResponse(
            {"resource": cfg.mcp_resource,
             "authorization_servers": [cfg.mcp_issuer],
             "scopes_supported": ["im19:read"]},
            headers={"Cache-Control": "public, max-age=300"},
        )
    return server


def create_app(settings: Settings | None = None):
    cfg = settings or Settings.from_env()
    cfg.require_remote()
    host = urlsplit(cfg.mcp_resource).hostname
    if not host:
        raise ValueError("resource_hostname_required")
    app = create_gateway(cfg).streamable_http_app(
        transport_security=TransportSecuritySettings(allowed_hosts=[host]),
        max_request_body_size=1_048_576,
    )
    return app


def main() -> None:
    cfg = Settings.from_env()
    cfg.require_remote()
    uvicorn.run(create_app(cfg), host=cfg.bind_host, port=cfg.port,
                proxy_headers=False, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
