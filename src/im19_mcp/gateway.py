"""Authenticated Streamable HTTP resource server for IM19.

ChatGPT -> MCP OAuth is distinct from Google OAuth. All /mcp requests
require opaque bearer token introspection from a trusted authorization server.
"""
from __future__ import annotations

import logging
import math
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
import uvicorn
from sqlalchemy.exc import SQLAlchemyError
from cryptography.fernet import InvalidToken
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.responses import RedirectResponse

from im19_mcp.config import Settings
from im19_mcp import __version__
from im19_mcp.browser_auth import BrowserAuthorization, COOKIE_NAME, browser_digest
from im19_mcp.http_security import GatewaySecurityMiddleware
from im19_mcp.outbound import request_json
from im19_mcp.google_oauth import GoogleOAuth, OAuthError
from im19_mcp.distributed_quota import DistributedQuota
from im19_mcp.rate_limit import DistributedRateLimitMiddleware, RateLimitMiddleware
from im19_mcp.readiness import database_ready
from im19_mcp.server import register_tools
from im19_mcp.storage import Vault

logger = logging.getLogger(__name__)


class IntrospectionVerifier(TokenVerifier):
    def __init__(self, cfg: Settings):
        self.cfg = cfg

    async def verify_token(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or not 1 <= len(token) <= 8192:
            return None
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                body = await request_json(
                    "POST", self.cfg.mcp_introspection, client=client, max_bytes=16384, timeout=5,
                    data={"token": token},
                    auth=(self.cfg.mcp_client_id, self.cfg.mcp_client_secret),
                    headers={"Accept": "application/json"},
                )
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
        if (not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or
                not time.time() < expiry <= 253402300799 or not math.isfinite(expiry)):
            return None
        not_before = body.get("nbf", 0)
        if (not isinstance(not_before, (int, float)) or isinstance(not_before, bool) or
                not 0 <= not_before <= time.time() or not math.isfinite(not_before)):
            return None
        if any(not isinstance(body.get(key), str) or not body[key].strip() or
               len(body[key]) > 255 for key in ("client_id", "sub")):
            return None
        scope = body.get("scope")
        scopes = scope.split() if isinstance(scope, str) else []
        if "im19:read" not in scopes:
            return None
        return AccessToken(
            token=token, client_id=body["client_id"], subject=body["sub"],
            resource=self.cfg.mcp_resource, scopes=scopes, expires_at=int(expiry),
        )


def create_gateway(
    settings: Settings | None = None, *, quota: DistributedQuota | None = None,
    vault: Vault | None = None,
) -> MCPServer:
    cfg = settings or Settings.from_env()
    cfg.require_remote()
    vault = vault or Vault(
        cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt,
        previous_keys=cfg.vault_previous_keys,
    )
    # Database schema must be created through an explicit migration/bootstrap step.
    oauth = GoogleOAuth(cfg, vault, require_browser=cfg.deployment_environment == "production") if cfg.google_enabled() else None
    verifier = IntrospectionVerifier(cfg)
    browser = BrowserAuthorization(cfg, vault, oauth, verifier) if oauth and cfg.browser_enabled() else None
    server = MCPServer(
        "IM19 Universal MCP",
        version=__version__,
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(cfg.mcp_issuer),
            resource_server_url=AnyHttpUrl(cfg.mcp_resource),
            required_scopes=["im19:read"],
            validate_token_resource=True,
        ),
    )
    effective_quota = (
        quota if quota is not None else
        DistributedQuota(vault.engine, secret=cfg.audit_salt)
        if cfg.deployment_environment == "production" else None
    )
    register_tools(server, settings=cfg, vault=vault, oauth=oauth,
                   distributed_quota=effective_quota, browser_auth=browser)

    @server.custom_route("/connect/google", methods=["GET"])
    async def browser_start(request: Request):
        if browser is None:
            return JSONResponse({"status": "NOT_CONFIGURED"}, status_code=503)
        if len(request.query_params.getlist("ticket")) != 1:
            return JSONResponse({"status": "OAUTH_FAILED"}, status_code=400)
        try:
            location, cookie = await browser.start(request.query_params["ticket"])
            response = RedirectResponse(location, status_code=303)
            response.set_cookie(COOKIE_NAME, cookie, max_age=600, secure=True,
                                httponly=True, samesite="lax", path="/")
            return response
        except (OAuthError, PermissionError, ValueError):
            return JSONResponse({"status": "OAUTH_FAILED"}, status_code=400)
        except (SQLAlchemyError, InvalidToken):
            return JSONResponse({"status": "SERVICE_UNAVAILABLE"}, status_code=503)

    @server.custom_route("/oauth/browser/callback", methods=["GET"])
    async def browser_callback(request: Request):
        if browser is None:
            return JSONResponse({"status": "NOT_CONFIGURED"}, status_code=503)
        if (len(request.query_params.getlist("state")) != 1 or
                len(request.query_params.getlist("code")) != 1 or request.query_params.get("error")):
            return JSONResponse({"status": "OAUTH_FAILED"}, status_code=400)
        try:
            result = await browser.complete(state=request.query_params["state"],
                                            code=request.query_params["code"],
                                            cookie=request.cookies.get(COOKIE_NAME, ""))
            return RedirectResponse(result["authorization_url"], status_code=303)
        except (OAuthError, PermissionError, ValueError):
            return JSONResponse({"status": "OAUTH_FAILED"}, status_code=400)
        except (SQLAlchemyError, InvalidToken):
            return JSONResponse({"status": "SERVICE_UNAVAILABLE"}, status_code=503)

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "im19-universal-mcp"},
                            headers={"Cache-Control": "no-store"})

    @server.custom_route("/ready", methods=["GET"])
    async def ready(_: Request) -> JSONResponse:
        """Return 503 until database is reachable and schema is current."""
        healthy = await run_in_threadpool(database_ready, vault.engine)
        return JSONResponse(
            {"status": "ready" if healthy else "unavailable"},
            status_code=200 if healthy else 503,
            headers={"Cache-Control": "no-store"},
        )

    @server.custom_route("/oauth/google/callback", methods=["GET"])
    async def google_callback(request: Request) -> JSONResponse:
        """Public callback stages a link; ONLY an authenticated MCP tool can commit it.

        OAuth state and authorization code arrive in URL query parameters.
        Disable request access logs at all ingress layers.
        """
        headers = {
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; form-action 'none'",
        }
        if oauth is None:
            return JSONResponse({"status": "NOT_CONFIGURED"}, status_code=503, headers=headers)
        if request.query_params.get("error"):
            return JSONResponse({"status": "DENIED_BY_PROVIDER"}, status_code=400, headers=headers)
        if len(request.query_params.getlist("state")) != 1 or len(request.query_params.getlist("code")) != 1:
            return JSONResponse({"status": "OAUTH_FAILED"}, status_code=400, headers=headers)
        state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
        try:
            cookie = request.cookies.get(COOKIE_NAME, "")
            binding = browser_digest(cookie) if cookie else ""
            if cfg.deployment_environment == "production" and not binding:
                raise OAuthError("browser_session_required")
            # Preserve staging callback API when no browser flow is enabled.
            pending = await oauth.complete(state=state, code=code, **({"browser_hash": binding} if binding else {}))
            response = JSONResponse(pending, headers=headers)
            response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite="lax", path="/")
            return response
        except (OAuthError, PermissionError, ValueError):
            return JSONResponse({"status": "OAUTH_FAILED"}, status_code=400, headers=headers)
        except (SQLAlchemyError, InvalidToken):
            return JSONResponse({"status": "SERVICE_UNAVAILABLE"}, status_code=503, headers=headers)

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
    resource = urlsplit(cfg.mcp_resource)
    host = resource.netloc
    if not host:
        raise ValueError("resource_hostname_required")
    vault = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt,
                  previous_keys=cfg.vault_previous_keys)
    # One bounded pool per worker for shared HTTP/tool quotas and credentials.
    quota = (
        DistributedQuota(vault.engine,
                         secret=cfg.audit_salt)
        if cfg.deployment_environment == "production" else None
    )
    app = create_gateway(cfg, quota=quota, vault=vault).streamable_http_app(
        transport_security=TransportSecuritySettings(
            allowed_hosts=[host] + ([resource.hostname + ":443"] if resource.port is None else []),
            allowed_origins=[resource.scheme + "://" + host],
        ),
        stateless_http=True,
        json_response=True,
        max_request_body_size=1_048_576,
    )
    if quota is not None:
        app.add_middleware(DistributedRateLimitMiddleware, quota=quota,
                           requests_per_minute=120)
    else:
        app.add_middleware(RateLimitMiddleware, requests_per_minute=120,
                           max_clients=4096)
    # Add last so invalid hosts/origins cannot reach auth, quotas or callbacks.
    app.add_middleware(GatewaySecurityMiddleware, resource=cfg.mcp_resource,
                       production=cfg.deployment_environment == "production")
    sdk_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with sdk_lifespan(app) as state:
                yield state
        finally:
            await run_in_threadpool(vault.engine.dispose)

    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    cfg = Settings.from_env()
    cfg.require_remote()
    app = create_app(cfg)
    # HTTP client INFO/DEBUG logs include query strings. Keep upstream request
    # metadata out of default production logs, even after SDK log setup.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    uvicorn.run(app, host=cfg.bind_host, port=cfg.port,
                proxy_headers=False, timeout_keep_alive=5, access_log=False)


if __name__ == "__main__":
    main()

