"""First-party, OAuth-verified approval of an exact Drive write.

A bearer request link is NOT an authorization. Approvals require an independent
Authorization Code + PKCE login, matching MCP user, browser cookie, CSRF token
and an explicit POST click. The one-time ledger grant never leaves the server.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from mcp.server.auth.provider import TokenVerifier
from sqlalchemy import Float, String, Text, select, update
from sqlalchemy.orm import Mapped, mapped_column
from starlette.concurrency import run_in_threadpool

from im19_mcp.config import Settings
from im19_mcp.drive import validate_folder_name
from im19_mcp.governance.approvals import ApprovalLedger, payload_sha256
from im19_mcp.outbound import request_json
from im19_mcp.storage import Base, Vault

WRITE_COOKIE = "__Host-im19-write-review"
FOLDER_MIME = "application/vnd.google-apps.folder"
ACTION = "drive.create_folder"


def digest(value: str) -> str:
    if not isinstance(value, str) or not 32 <= len(value) <= 256 or not value.isascii():
        raise PermissionError("write_approval_invalid")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class WriteApprovalRequest(Base):
    __tablename__ = "write_approval_requests"

    ticket_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    oauth_state_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    verifier_cipher: Mapped[str | None] = mapped_column(Text)
    browser_hash: Mapped[str | None] = mapped_column(String(64))
    csrf_hash: Mapped[str | None] = mapped_column(String(64))
    approval_cipher: Mapped[str | None] = mapped_column(Text)


class WriteApprovalPortal:
    """Trusted, separately authenticated browser approval; no tool can issue grants."""

    def __init__(self, cfg: Settings, vault: Vault, verifier: TokenVerifier,
                 http_client: httpx.AsyncClient | None = None):
        if not cfg.browser_enabled():
            raise ValueError("write_approval_requires_browser_identity_provider")
        self.cfg, self.vault, self.verifier, self.http_client = cfg, vault, verifier, http_client
        self.ledger = ApprovalLedger(vault)
        self.origin = "https://" + urlsplit(cfg.mcp_resource).netloc

    def request(self, *, subject: str, account_id: str, name: str) -> dict:
        validate_folder_name(name)
        if not subject or len(subject) > 255 or not account_id or len(account_id) > 255:
            raise PermissionError("verified_identity_required")
        ticket = secrets.token_urlsafe(32)
        payload = {"name": name, "mimeType": FOLDER_MIME}
        with self.vault.sessions.begin() as session:
            session.add(WriteApprovalRequest(
                ticket_hash=digest(ticket), subject=subject, account_id=account_id,
                name_cipher=self.vault.encrypt(name), payload_hash=payload_sha256(payload),
                idempotency_key="write-" + secrets.token_hex(24),
                status="PENDING", expires_at=time.time() + 300,
            ))
        return {
            "status": "APPROVAL_REQUIRED",
            "approval_request_id": ticket,
            "approval_url": self.origin + "/approve/write?" + urlencode({"ticket": ticket}),
            "expires_in": 300,
            "service": "drive",
            "action": ACTION,
            "details": {"name": name, "location": "My Drive root", "operation": "create folder"},
            "instructions": "Open the approval_url in your browser, login as the same IM19 user, "
                            "review and choose Approve or Deny. Then call drive_create_folder "
                            "with the identical name and approval_request_id.",
        }

    def begin(self, ticket: str) -> tuple[str, str]:
        key = digest(ticket)
        state, verifier, cookie = (secrets.token_urlsafe(32), secrets.token_urlsafe(48),
                                   secrets.token_urlsafe(32))
        pkce = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
        now = time.time()
        with self.vault.sessions.begin() as session:
            row = session.execute(select(WriteApprovalRequest).where(
                WriteApprovalRequest.ticket_hash == key
            ).with_for_update()).scalar_one_or_none()
            if row is None or row.status not in {"PENDING", "LOGIN"} or row.expires_at <= now:
                raise PermissionError("approval_request_invalid_or_expired")
            row.status = "LOGIN"
            row.oauth_state_hash = digest(state)
            row.verifier_cipher = self.vault.encrypt(verifier)
            row.browser_hash = digest(cookie)
            row.csrf_hash = None
        params = {
            "response_type": "code", "client_id": self.cfg.browser_client_id,
            "redirect_uri": self.origin + "/approve/write/callback",
            "scope": "im19:read", "resource": self.cfg.mcp_resource,
            "state": state, "code_challenge": pkce, "code_challenge_method": "S256",
        }
        return self.cfg.browser_authorization + "?" + urlencode(params), cookie

    def _consume_state(self, state: str, cookie: str) -> tuple[str, str, str]:
        sh, ch = digest(state), digest(cookie)
        with self.vault.sessions.begin() as session:
            row = session.execute(select(WriteApprovalRequest).where(
                WriteApprovalRequest.oauth_state_hash == sh
            ).with_for_update()).scalar_one_or_none()
            if (row is None or row.status != "LOGIN" or row.expires_at <= time.time() or
                    not row.browser_hash or not hmac.compare_digest(row.browser_hash, ch) or
                    not row.verifier_cipher):
                raise PermissionError("write_login_invalid_or_replayed")
            row.status = "VERIFYING"
            row.oauth_state_hash = None  # callback may never be redeemed again
            return row.ticket_hash, row.subject, str(self.vault.decrypt(row.verifier_cipher))

    async def callback(self, *, state: str, code: str, cookie: str) -> tuple[str, str, str]:
        if not isinstance(code, str) or not 1 <= len(code) <= 4096:
            raise PermissionError("write_login_code_invalid")
        key, subject, pkce = await run_in_threadpool(self._consume_state, state, cookie)
        try:
            token = await request_json(
                "POST", self.cfg.browser_token, client=self.http_client,
                timeout=10, max_bytes=16384,
                auth=(self.cfg.browser_client_id, self.cfg.browser_client_secret),
                data={"grant_type": "authorization_code", "code": code,
                      "redirect_uri": self.origin + "/approve/write/callback",
                      "code_verifier": pkce, "resource": self.cfg.mcp_resource},
            )
            access = token.get("access_token")
            if (not isinstance(access, str) or not 1 <= len(access) <= 8192 or
                    token.get("token_type", "").lower() != "bearer"):
                raise PermissionError("write_login_token_invalid")
            verified = await self.verifier.verify_token(access)
        except (httpx.HTTPError, ValueError, AttributeError):
            raise PermissionError("write_login_failed") from None
        if (verified is None or verified.subject != subject or
                verified.client_id != self.cfg.browser_client_id or
                "im19:read" not in verified.scopes):
            raise PermissionError("write_login_subject_mismatch")
        csrf = secrets.token_urlsafe(32)
        with self.vault.sessions.begin() as session:
            row = session.execute(select(WriteApprovalRequest).where(
                WriteApprovalRequest.ticket_hash == key
            ).with_for_update()).scalar_one_or_none()
            if row is None or row.status != "VERIFYING" or row.expires_at <= time.time():
                raise PermissionError("write_login_expired")
            row.status = "REVIEW"
            row.csrf_hash = digest(csrf)
            # Browser proof is retained ONLY as a hash, not an access token.
            name = self.vault.decrypt(row.name_cipher)
        return key, csrf, str(name)

    def decide(self, *, request_key: str, cookie: str, csrf: str, decision: str) -> str:
        if decision not in {"approve", "deny"}:
            raise PermissionError("write_decision_invalid")
        if not isinstance(request_key, str) or len(request_key) != 64 or any(
            c not in "0123456789abcdef" for c in request_key
        ):
            raise PermissionError("write_request_invalid")
        csrf_hash, cookie_hash = digest(csrf), digest(cookie)
        # Consume CSRF once and freeze request before issuing a backend-only grant.
        with self.vault.sessions.begin() as session:
            row = session.execute(select(WriteApprovalRequest).where(
                WriteApprovalRequest.ticket_hash == request_key
            ).with_for_update()).scalar_one_or_none()
            if (row is None or row.status != "REVIEW" or row.expires_at <= time.time() or
                    not row.csrf_hash or not row.browser_hash or
                    not hmac.compare_digest(row.csrf_hash, csrf_hash) or
                    not hmac.compare_digest(row.browser_hash, cookie_hash)):
                raise PermissionError("write_approval_not_authenticated")
            row.status = "DENIED" if decision == "deny" else "ISSUING"
            row.csrf_hash = None
            if decision == "deny":
                return "DENIED"
            subject, account_id, payload_hash = row.subject, row.account_id, row.payload_hash
        # This trusted backend issues the approval, never the model/client.
        grant = self.ledger.issue(
            subject=subject, provider="drive", account_id=account_id,
            action=ACTION, resource_id="root", payload_hash=payload_hash,
            approver=subject, evidence_reference="browser-oauth-" + secrets.token_hex(16),
            ttl_seconds=300,
        )
        with self.vault.sessions.begin() as session:
            row = session.execute(select(WriteApprovalRequest).where(
                WriteApprovalRequest.ticket_hash == request_key
            ).with_for_update()).scalar_one_or_none()
            if row is None or row.status != "ISSUING" or row.expires_at <= time.time():
                raise PermissionError("write_approval_expired")
            row.approval_cipher = self.vault.encrypt(grant)
            row.status = "APPROVED"
        return "APPROVED"

    def consume(self, *, ticket: str, subject: str, account_id: str, name: str) -> tuple[str, str]:
        """Return grant ONLY to trusted server-side orchestration, never MCP client."""
        key = digest(ticket)
        payload_hash = payload_sha256({"name": validate_folder_name(name), "mimeType": FOLDER_MIME})
        with self.vault.sessions.begin() as session:
            row = session.execute(select(WriteApprovalRequest).where(
                WriteApprovalRequest.ticket_hash == key
            ).with_for_update()).scalar_one_or_none()
            if (row is None or row.subject != subject or row.account_id != account_id or
                    not hmac.compare_digest(row.payload_hash, payload_hash)):
                raise PermissionError("write_approval_scope_mismatch")
            if row.expires_at <= time.time():
                raise PermissionError("write_approval_expired")
            if row.status == "DENIED":
                raise PermissionError("write_approval_denied")
            if row.status != "APPROVED" or not row.approval_cipher:
                raise PermissionError("write_approval_pending_or_consumed")
            row.status = "CLAIMED"
            return str(self.vault.decrypt(row.approval_cipher)), row.idempotency_key

    @staticmethod
    def review_html(*, request_key: str, csrf: str, name: str) -> str:
        safe_name = html.escape(name, quote=True)
        # No third-party scripts or external form targets.
        return (
            "<!doctype html><html lang='id'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Persetujuan IM19</title><body><main>"
            "<h1>Setujui perubahan Google Drive?</h1>"
            "<p>Aksi: buat folder baru di lokasi utama Google Drive.</p>"
            f"<p>Nama folder: <strong>{safe_name}</strong></p>"
            "<p>Tidak ada perubahan yang dibuat sebelum Anda menyetujui.</p>"
            "<form method='post' action='/approve/write/decision'>"
            f"<input type='hidden' name='request' value='{request_key}'>"
            f"<input type='hidden' name='csrf' value='{csrf}'>"
            "<button type='submit' name='decision' value='approve'>Setujui</button>"
            "<button type='submit' name='decision' value='deny'>Tolak</button>"
            "</form></main></body></html>"
        )
