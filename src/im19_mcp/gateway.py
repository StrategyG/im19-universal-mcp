"""Authenticated Streamable HTTP resource server for IM19.

ChatGPT -> MCP OAuth is distinct from Google OAuth. All /mcp requests
require opaque bearer token introspection from a trusted authorization server.
"""
from __future__ import annotations

import logging
import math
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, parse_qs

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
from starlette.responses import RedirectResponse, HTMLResponse

from im19_mcp.config import Settings
from im19_mcp import __version__
from im19_mcp.browser_auth import BrowserAuthorization, COOKIE_NAME, browser_digest
from im19_mcp.http_security import GatewaySecurityMiddleware
from im19_mcp.outbound import request_json
from im19_mcp.google_oauth import GoogleOAuth, OAuthError
from im19_mcp.governance.write_approval import WriteApprovalPortal, WRITE_COOKIE
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


async def _bounded_approval_form(request: Request, *, limit: int = 2048) -> bytes:
    """Bound form bytes even when clients omit or forge Content-Length.

    Reject ambiguous/invalid lengths and stop reading after the first
    oversized stream chunk. Never store an unbounded request body in memory.
    """
    lengths = [
        value for key, value in request.scope.get("headers", [])
        if key.lower() == b"content-length"
    ]
    if len(lengths) > 1:
        raise ValueError("duplicate_content_length")
    declared = None
    if lengths:
        raw = lengths[0]
        if not raw or len(raw) > 8 or not raw.isdigit():
            raise ValueError("invalid_content_length")
        declared = int(raw)
        if declared > limit:
            raise ValueError("approval_form_too_large")
    received = bytearray()
    async for chunk in request.stream():
        if len(received) + len(chunk) > limit:
            raise ValueError("approval_form_too_large")
        received.extend(chunk)
    if declared is not None and len(received) != declared:
        raise ValueError("content_length_mismatch")
    return bytes(received)


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
    write_portal = (WriteApprovalPortal(cfg, vault, verifier)
                    if cfg.enable_approved_drive_writes and cfg.browser_enabled() and oauth else None)
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
                   distributed_quota=effective_quota, browser_auth=browser,
                   write_approval_portal=write_portal)

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

    @server.custom_route("/approve/write", methods=["GET"])
    async def write_review_start(request: Request):
        if write_portal is None:
            return JSONResponse({"status": "NOT_CONFIGURED"}, status_code=503)
        if len(request.query_params.getlist("ticket")) != 1:
            return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
        try:
            location, cookie = await run_in_threadpool(
                write_portal.begin, request.query_params["ticket"]
            )
        except (PermissionError, ValueError):
            return JSONResponse({"status": "APPROVAL_INVALID_OR_EXPIRED"}, status_code=403)
        except (SQLAlchemyError, InvalidToken):
            return JSONResponse({"status": "SERVICE_UNAVAILABLE"}, status_code=503)
        response = RedirectResponse(location, status_code=303)
        response.set_cookie(WRITE_COOKIE, cookie, max_age=300, secure=True,
                            httponly=True, samesite="lax", path="/")
        return response

    @server.custom_route("/approve/write/callback", methods=["GET"])
    async def write_review_callback(request: Request):
        if write_portal is None:
            return JSONResponse({"status": "NOT_CONFIGURED"}, status_code=503)
        if (request.query_params.get("error") or
                len(request.query_params.getlist("state")) != 1 or
                len(request.query_params.getlist("code")) != 1):
            return JSONResponse({"status": "APPROVAL_LOGIN_INVALID"}, status_code=400)
        try:
            key, csrf, name = await write_portal.callback(
                state=request.query_params["state"], code=request.query_params["code"],
                cookie=request.cookies.get(WRITE_COOKIE, ""),
            )
            return HTMLResponse(write_portal.review_html(request_key=key, csrf=csrf, name=name),
                                headers={"Cache-Control": "no-store, private"})
        except (PermissionError, ValueError, httpx.HTTPError):
            return JSONResponse({"status": "APPROVAL_LOGIN_INVALID"}, status_code=403)
        except (SQLAlchemyError, InvalidToken):
            return JSONResponse({"status": "SERVICE_UNAVAILABLE"}, status_code=503)

    @server.custom_route("/approve/write/decision", methods=["POST"])
    async def write_review_decision(request: Request):
        if write_portal is None:
            return JSONResponse({"status": "NOT_CONFIGURED"}, status_code=503)
        # HTML form POST requires explicit same-origin and CSRF: no MCP/API
        # client can approve by invoking a tool or by possessing the ticket.
        if (request.headers.get("origin") != write_portal.origin or
                not request.headers.get("content-type", "").startswith(
                    "application/x-www-form-urlencoded"
                )):
            return JSONResponse({"status": "APPROVAL_DENIED"}, status_code=403)
        try:
            data = await _bounded_approval_form(request)
            values = parse_qs(data.decode("ascii"), keep_blank_values=True, max_num_fields=5)
            if any(len(values.get(key, [])) != 1 for key in ("request", "csrf", "decision")):
                raise ValueError("missing_or_duplicate_fields")
            decision = await run_in_threadpool(
                write_portal.decide, request_key=values["request"][0],
                csrf=values["csrf"][0], decision=values["decision"][0],
                cookie=request.cookies.get(WRITE_COOKIE, ""),
            )
        except (UnicodeError, ValueError, PermissionError):
            return JSONResponse({"status": "APPROVAL_DENIED"}, status_code=403)
        except (SQLAlchemyError, InvalidToken):
            return JSONResponse({"status": "SERVICE_UNAVAILABLE"}, status_code=503)
        msg = ("Persetujuan tersimpan. Kembali ke percakapan AI dan lanjutkan permintaan."
               if decision == "APPROVED" else "Permintaan write ditolak. Tidak ada perubahan dilakukan.")
        response = HTMLResponse(
            "<!doctype html><html lang='id'><meta charset='utf-8'>"
            "<title>IM19</title><body><h1>IM19</h1><p>" + msg + "</p></body></html>",
            headers={"Cache-Control": "no-store, private"},
        )
        response.delete_cookie(WRITE_COOKIE, secure=True, httponly=True,
                               samesite="lax", path="/")
        return response

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

