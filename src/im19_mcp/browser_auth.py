"""First-party browser proof before Google consent; never trust a link ticket as login.

The configured authorization server issues a resource-bound access token via
Authorization Code + PKCE. Introspection must prove the SAME subject that
requested linking over MCP, and the dedicated browser client. No ID token is
used as proof of identity. All state lives in the shared encrypted vault.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from mcp.server.auth.provider import TokenVerifier
from starlette.concurrency import run_in_threadpool

from im19_mcp.config import Settings
from im19_mcp.google_oauth import GoogleOAuth, OAuthError, SCOPES
from im19_mcp.outbound import request_json
from im19_mcp.storage import Vault

COOKIE_NAME = "__Host-im19-link"


def browser_digest(cookie: str) -> str:
    if not isinstance(cookie, str) or not 32 <= len(cookie) <= 128 or not cookie.isascii():
        raise OAuthError("browser_session_required")
    return hashlib.sha256(cookie.encode("ascii")).hexdigest()


class BrowserAuthorization:
    def __init__(self, settings: Settings, vault: Vault, oauth: GoogleOAuth,
                 verifier: TokenVerifier, client: httpx.AsyncClient | None = None):
        self.cfg, self.vault, self.oauth, self.verifier = settings, vault, oauth, verifier
        self.client = client
        self.origin = "https://" + urlsplit(settings.mcp_resource).netloc

    def ticket(self, subject: str, service: str, *, analytics: bool = False,
               include_content: bool = False) -> dict:
        scopes = GoogleOAuth.requested_scopes(service, analytics=analytics,
                                              include_content=include_content)
        ticket = secrets.token_urlsafe(32)
        self.vault.create_state(ticket, subject, scopes, secrets.token_urlsafe(48),
                                time.time() + 600, service=service, purpose="browser-ticket")
        return {"status": "AUTH_REQUIRED", "service": service,
                "authorization_url": self.origin + "/connect/google?" + urlencode({"ticket": ticket}),
                "expires_in": 600, "requested_scopes": list(scopes),
                "browser_login_required": True}

    async def start(self, ticket: str) -> tuple[str, str]:
        if not 20 <= len(ticket) <= 256:
            raise OAuthError("browser_ticket_invalid")
        subject, service, scopes, _ = await run_in_threadpool(
            self.vault.consume_state, ticket, purpose="browser-ticket",
        )
        state, verifier, cookie = (secrets.token_urlsafe(32), secrets.token_urlsafe(48),
                                   secrets.token_urlsafe(32))
        await run_in_threadpool(
            self.vault.create_state, state, subject, scopes, verifier, time.time() + 600,
            service=service, purpose="browser-login", browser_hash=browser_digest(cookie),
        )
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=")
        params = {"response_type": "code", "client_id": self.cfg.browser_client_id,
                  "redirect_uri": self.origin + "/oauth/browser/callback",
                  "scope": "im19:read", "resource": self.cfg.mcp_resource,
                  "state": state, "code_challenge": challenge.decode("ascii"),
                  "code_challenge_method": "S256"}
        return self.cfg.browser_authorization + "?" + urlencode(params), cookie

    async def complete(self, *, state: str, code: str, cookie: str) -> dict:
        if not 20 <= len(state) <= 256 or not 1 <= len(code) <= 4096:
            raise OAuthError("browser_callback_invalid")
        digest = browser_digest(cookie)
        subject, service, scopes, pkce = await run_in_threadpool(
            self.vault.consume_state, state, purpose="browser-login", browser_hash=digest,
        )
        try:
            tokens = await request_json(
                "POST", self.cfg.browser_token, client=self.client, max_bytes=16384, timeout=10,
                data={"grant_type": "authorization_code", "code": code,
                      "redirect_uri": self.origin + "/oauth/browser/callback",
                      "code_verifier": pkce, "resource": self.cfg.mcp_resource},
                auth=(self.cfg.browser_client_id, self.cfg.browser_client_secret),
            )
            access = tokens.get("access_token")
            if (not isinstance(access, str) or not 1 <= len(access) <= 8192 or
                    tokens.get("token_type", "").lower() != "bearer"):
                raise OAuthError("browser_login_failed")
            identity = await self.verifier.verify_token(access)
        except (httpx.HTTPError, ValueError, AttributeError):
            raise OAuthError("browser_login_failed") from None
        if (identity is None or identity.subject != subject or
                identity.client_id != self.cfg.browser_client_id):
            raise OAuthError("browser_identity_mismatch")
        # Never persist/return the browser access token. Only this browser may
        # redeem the subsequent Google callback, for this verified subject.
        return await run_in_threadpool(
            self.oauth.begin, subject, service=service,
            analytics=SCOPES["youtube_analytics"][0] in scopes,
            include_content=SCOPES["drive_content"][0] in scopes, browser_hash=digest,
        )
