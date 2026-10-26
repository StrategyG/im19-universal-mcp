"""Google OAuth with PKCE, one-time DB state, encrypted tokens and revocation.

This is provider authorization (IM19 -> Google), NOT ChatGPT -> IM19 auth.
Production requires a separately validated MCP principal and HTTPS callback.
"""
from __future__ import annotations

import base64
import hashlib
import math
import secrets
import time
from urllib.parse import urlencode

import httpx
from starlette.concurrency import run_in_threadpool

from im19_mcp.config import Settings
from im19_mcp.storage import Vault
from im19_mcp.outbound import request_json

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # nosec B105
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = {
    "youtube": ("https://www.googleapis.com/auth/youtube.readonly",),
    "youtube_analytics": ("https://www.googleapis.com/auth/yt-analytics.readonly",),
    "drive_metadata": ("https://www.googleapis.com/auth/drive.metadata.readonly",),
    "drive_content": ("https://www.googleapis.com/auth/drive.readonly",),
}
OPENID = "openid"


class OAuthError(Exception):
    """Sanitized OAuth error that never includes provider response or credentials."""


class GoogleOAuth:
    def __init__(self, settings: Settings, vault: Vault, http_client: httpx.AsyncClient | None = None,
                 *, require_browser: bool = False):
        if not settings.google_enabled():
            raise ValueError("google_oauth_not_configured")
        self.settings, self.vault = settings, vault
        self.client = http_client
        self.require_browser = require_browser

    def begin(
        self, subject: str, *, service: str = "youtube",
        analytics: bool = False, include_content: bool = False, browser_hash: str = "",
    ) -> dict:
        """Request only scopes needed by this service and explicit elevated options.

        drive.readonly is a RESTRICTED Google scope; never add it implicitly.
        """
        if not subject:
            raise PermissionError("authenticated_subject_required")
        scopes = self.requested_scopes(service, analytics=analytics, include_content=include_content)
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.vault.create_state(state, subject, scopes, verifier, time.time() + 600,
                                service=service, browser_hash=browser_hash)
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "include_granted_scopes": "true",
        }
        return {"status": "AUTH_REQUIRED", "service": service,
                "authorization_url": AUTH_URL + "?" + urlencode(params),
                "expires_in": 600, "requested_scopes": list(scopes)}

    @staticmethod
    def requested_scopes(service: str, *, analytics: bool = False,
                         include_content: bool = False) -> tuple[str, ...]:
        if service not in {"youtube", "drive"}:
            raise ValueError("google_service_unsupported")
        if (service != "youtube" and analytics) or (service != "drive" and include_content):
            raise ValueError("google_scope_option_invalid")
        base = SCOPES["youtube"] if service == "youtube" else SCOPES["drive_metadata"]
        elevated = SCOPES["youtube_analytics"] if analytics else SCOPES["drive_content"] if include_content else ()
        return tuple(sorted(set((OPENID,) + base + elevated)))

    @staticmethod
    def _lifetime(value) -> float:
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or
                not 1 <= value <= 86400 or not math.isfinite(value)):
            raise OAuthError("google_token_expiry_invalid")
        return float(value)

    async def _post(self, url: str, data: dict) -> dict:
        try:
            value = await request_json("POST", url, client=self.client, data=data,
                                       max_bytes=32768, timeout=10)
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthError("google_oauth_provider_unavailable_or_rejected") from exc
        if not isinstance(value, dict):
            raise OAuthError("google_oauth_response_invalid")
        return value

    async def _userinfo(self, access_token: str) -> str:
        try:
            value = await request_json("GET", USERINFO_URL, client=self.client, max_bytes=16384,
                                       timeout=10, headers={"Authorization": f"Bearer {access_token}"})
            subject = value.get("sub")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise OAuthError("google_identity_verification_failed") from exc
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise OAuthError("google_identity_missing")
        return subject

    async def complete(self, *, state: str, code: str, browser_hash: str = "") -> dict:
        if self.require_browser and not browser_hash:
            raise OAuthError("browser_session_required")
        if not state or len(state) > 256 or not code or len(code) > 4096:
            raise PermissionError("oauth_callback_invalid")
        subject, service, expected, verifier = await run_in_threadpool(
            self.vault.consume_state, state, browser_hash=browser_hash,
        )
        token = await self._post(TOKEN_URL, {
            "code": code,
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "redirect_uri": self.settings.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        })
        access = token.get("access_token")
        if not isinstance(access, str) or not 1 <= len(access) <= 8192:
            raise OAuthError("google_access_token_missing")
        if not isinstance(token.get("scope"), str):
            raise OAuthError("google_scope_insufficient")
        granted = frozenset(token["scope"].split())
        if not set(expected).issubset(granted):
            raise OAuthError("google_scope_insufficient")
        account_id = await self._userinfo(access)
        existing = await run_in_threadpool(self.vault.get_credential, subject, service)
        if existing and existing[0] != account_id:
            raise OAuthError("google_account_change_requires_disconnect")
        refresh = token.get("refresh_token") or (existing[1].get("refresh_token") if existing else "")
        data = {"access_token": access, "refresh_token": refresh, "scopes": sorted(granted),
                "browser_verified": bool(browser_hash)}
        lifetime = self._lifetime(token.get("expires_in"))
        confirmation_code = await run_in_threadpool(
            self.vault.stage_link, subject, service, account_id, data, time.time() + lifetime,
        )
        await run_in_threadpool(self.vault.audit, subject, service, "oauth.callback", "PENDING_CONFIRMATION")
        return {
            "status": "PENDING_CONFIRMATION",
            "service": service,
            "account_id": account_id,
            "confirmation_code": confirmation_code,
            "expires_in": 300,
            "instructions": (
                "Copy this one-time code into IM19 "
                "confirm_service_authorization. Never share the code with others."
            ),
        }

    def confirm(self, subject: str, service: str, confirmation_code: str) -> dict:
        """Final link only after the originating MCP principal confirms the code.

        A Google OAuth callback alone NEVER saves a usable provider connection.
        """
        try:
            account_id, data, expiry = self.vault.activate_link(
                subject, service, confirmation_code,
                require_browser=self.require_browser,
            )
        except (PermissionError, ValueError) as exc:
            self.vault.audit(subject, service, "oauth.confirm", "DENY")
            raise OAuthError("confirmation_invalid_or_expired") from exc
        return {
            "status": "CONNECTED", "service": service, "account_id": account_id,
            "scopes": list(data.get("scopes", [])),
        }

    async def access_token(
        self, subject: str, required_scopes: set[str], *, service: str = "youtube"
    ) -> tuple[str, str]:
        if service not in {"youtube", "drive"}:
            raise OAuthError("UNKNOWN_SERVICE")
        found = await run_in_threadpool(self.vault.get_credential, subject, service)
        if found is None:
            raise OAuthError("AUTH_REQUIRED")
        account_id, data, expires = found
        if self.require_browser and data.get("browser_verified") is not True:
            raise OAuthError("AUTH_REQUIRED")
        if not required_scopes.issubset(set(data.get("scopes", []))):
            raise OAuthError("SCOPE_REQUIRED")
        if expires > time.time() + 60:
            return account_id, data["access_token"]

        # A DB-backed lease prevents concurrent refresh requests across workers.
        # Recheck the credential under the lease: another request might have
        # refreshed the token since the first read.
        lease_owner = secrets.token_urlsafe(24)
        locked = await run_in_threadpool(
            self.vault.acquire_refresh_lease, subject, service, lease_owner, now=time.time(), lease_seconds=30,
        )
        if locked is None:
            raise OAuthError("REFRESH_IN_PROGRESS")
        try:
            locked_account, data, locked_expiry = locked
            if self.require_browser and data.get("browser_verified") is not True:
                raise OAuthError("AUTH_REQUIRED")
            if locked_expiry > time.time() + 60:
                if not required_scopes.issubset(set(data.get("scopes", []))):
                    raise OAuthError("SCOPE_REQUIRED")
                return locked_account, data["access_token"]
            if not required_scopes.issubset(set(data.get("scopes", []))):
                raise OAuthError("SCOPE_REQUIRED")
            refresh = data.get("refresh_token")
            if not refresh:
                raise OAuthError("AUTH_REQUIRED")
            updated = await self._post(TOKEN_URL, {
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            })
            access = updated.get("access_token")
            duration = updated.get("expires_in")
            if not isinstance(access, str) or not 1 <= len(access) <= 8192:
                raise OAuthError("AUTH_REQUIRED")
            duration = self._lifetime(duration)
            granted_value = updated.get("scope")
            granted = set(data.get("scopes", []))
            if granted_value is not None:
                if not isinstance(granted_value, str):
                    raise OAuthError("SCOPE_REQUIRED")
                granted = set(granted_value.split())
            if not required_scopes.issubset(granted):
                raise OAuthError("SCOPE_REQUIRED")
            fresh = {
                "access_token": access,
                "refresh_token": updated.get("refresh_token") or refresh,
                "scopes": sorted(granted),
                "browser_verified": data.get("browser_verified", False),
            }
            if not await run_in_threadpool(
                self.vault.complete_refresh, subject, service, lease_owner, locked_account, fresh,
                time.time() + duration,
            ):
                raise OAuthError("REFRESH_SUPERSEDED")
            return locked_account, access
        finally:
            await run_in_threadpool(self.vault.release_refresh_lease, subject, service, lease_owner)

    async def disconnect(self, subject: str, *, service: str = "youtube") -> bool:
        """Only call from a separately authorized human confirmation endpoint."""
        if service not in {"youtube", "drive"}:
            raise OAuthError("UNKNOWN_SERVICE")
        current = await run_in_threadpool(self.vault.get_credential, subject, service)
        if current is None:
            return False
        _account, data, _expiry = current
        token = data.get("refresh_token") or data.get("access_token")
        if token:
            try:
                from im19_mcp.outbound import request_bytes
                await request_bytes("POST", REVOKE_URL, client=self.client,
                                    data={"token": token}, max_bytes=16384, timeout=10)
            except (httpx.HTTPError, ValueError) as exc:
                raise OAuthError("google_revocation_failed_no_local_disconnect") from exc
        removed = await run_in_threadpool(self.vault.delete_credential, subject, service)
        await run_in_threadpool(self.vault.audit, subject, service, "oauth.disconnect", "SUCCESS")
        return removed

