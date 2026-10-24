"""Google OAuth with PKCE, one-time DB state, encrypted tokens and revocation.

This is provider authorization (IM19 -> Google), NOT ChatGPT -> IM19 auth.
Production requires a separately validated MCP principal and HTTPS callback.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx

from im19_mcp.config import Settings
from im19_mcp.storage import Vault

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
    def __init__(self, settings: Settings, vault: Vault, http_client: httpx.AsyncClient | None = None):
        if not settings.google_enabled():
            raise ValueError("google_oauth_not_configured")
        self.settings, self.vault = settings, vault
        self.client = http_client

    def begin(
        self, subject: str, *, service: str = "youtube",
        analytics: bool = False, include_content: bool = False
    ) -> dict:
        """Request only scopes needed by this service and explicit elevated options.

        drive.readonly is a RESTRICTED Google scope; never add it implicitly.
        """
        if not subject:
            raise PermissionError("authenticated_subject_required")
        if service not in {"youtube", "drive"}:
            raise ValueError("google_service_unsupported")
        if (service != "youtube" and analytics) or (service != "drive" and include_content):
            raise ValueError("google_scope_option_invalid")
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        base_scopes = SCOPES["youtube"] if service == "youtube" else SCOPES["drive_metadata"]
        elevated = (
            SCOPES["youtube_analytics"] if analytics
            else SCOPES["drive_content"] if include_content
            else ()
        )
        scopes = tuple(sorted(set((OPENID,) + base_scopes + elevated)))
        self.vault.create_state(state, subject, scopes, verifier, time.time() + 600, service=service)
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

    async def _post(self, url: str, data: dict) -> dict:
        try:
            if self.client is not None:
                response = await self.client.post(url, data=data)
            else:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    response = await client.post(url, data=data)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthError("google_oauth_provider_unavailable_or_rejected") from exc
        if not isinstance(value, dict):
            raise OAuthError("google_oauth_response_invalid")
        return value

    async def _userinfo(self, access_token: str) -> str:
        try:
            if self.client is not None:
                response = await self.client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
            else:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    response = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
            response.raise_for_status()
            subject = response.json().get("sub")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise OAuthError("google_identity_verification_failed") from exc
        if not isinstance(subject, str) or not subject:
            raise OAuthError("google_identity_missing")
        return subject

    async def complete(self, *, state: str, code: str) -> dict:
        if not state or len(state) > 256 or not code or len(code) > 4096:
            raise PermissionError("oauth_callback_invalid")
        subject, service, expected, verifier = self.vault.consume_state(state)
        token = await self._post(TOKEN_URL, {
            "code": code,
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "redirect_uri": self.settings.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        })
        access = token.get("access_token")
        if not isinstance(access, str) or not access:
            raise OAuthError("google_access_token_missing")
        granted = frozenset(str(token.get("scope", "")).split())
        if not set(expected).issubset(granted):
            raise OAuthError("google_scope_insufficient")
        account_id = await self._userinfo(access)
        existing = self.vault.get_credential(subject, service)
        if existing and existing[0] != account_id:
            raise OAuthError("google_account_change_requires_disconnect")
        refresh = token.get("refresh_token") or (existing[1].get("refresh_token") if existing else "")
        data = {"access_token": access, "refresh_token": refresh, "scopes": sorted(granted)}
        lifetime = token.get("expires_in", 0)
        if not isinstance(lifetime, (int, float)) or lifetime <= 0:
            raise OAuthError("google_token_expiry_invalid")
        self.vault.put_credential(subject, service, account_id, data, time.time() + lifetime)
        self.vault.audit(subject, service, "oauth.complete", "SUCCESS")
        return {"status": "CONNECTED", "service": service, "account_id": account_id,
                "scopes": sorted(granted)}

    async def access_token(
        self, subject: str, required_scopes: set[str], *, service: str = "youtube"
    ) -> tuple[str, str]:
        if service not in {"youtube", "drive"}:
            raise OAuthError("UNKNOWN_SERVICE")
        found = self.vault.get_credential(subject, service)
        if found is None:
            raise OAuthError("AUTH_REQUIRED")
        account_id, data, expires = found
        if not required_scopes.issubset(set(data.get("scopes", []))):
            raise OAuthError("SCOPE_REQUIRED")
        if expires > time.time() + 60:
            return account_id, data["access_token"]
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
        if not isinstance(access, str) or not isinstance(duration, (int, float)) or duration <= 0:
            raise OAuthError("AUTH_REQUIRED")
        fresh = {"access_token": access,
                 "refresh_token": updated.get("refresh_token") or refresh,
                 "scopes": data["scopes"]}
        self.vault.put_credential(subject, service, account_id, fresh, time.time() + duration)
        return account_id, access

    async def disconnect(self, subject: str, *, service: str = "youtube") -> bool:
        """Only call from a separately authorized human confirmation endpoint."""
        if service not in {"youtube", "drive"}:
            raise OAuthError("UNKNOWN_SERVICE")
        current = self.vault.get_credential(subject, service)
        if current is None:
            return False
        _account, data, _expiry = current
        token = data.get("refresh_token") or data.get("access_token")
        if token:
            try:
                if self.client is not None:
                    response = await self.client.post(REVOKE_URL, data={"token": token})
                else:
                    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                        response = await client.post(REVOKE_URL, data={"token": token})
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise OAuthError("google_revocation_failed_no_local_disconnect") from exc
        removed = self.vault.delete_credential(subject, service)
        self.vault.audit(subject, service, "oauth.disconnect", "SUCCESS")
        return removed
