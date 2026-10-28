"""Durable, owner-isolated READ-ONLY browser inspection queue.

MCP submits/status/cancels only. Browser execution happens in a separately
secured service process with *no* provider OAuth credentials, vault plaintext,
or arbitrary shell/HTTP tool access. Network policy/DNS egress confinement is
a DEPLOYMENT requirement; this module is not an SSRF network sandbox.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import Float, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from im19_mcp.browser_policy import BrowserPolicy, BrowserPolicyError
from im19_mcp.storage import Base, Vault

JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
KEY = re.compile(r"[A-Za-z0-9_-]{12,64}\Z")
MAX_ACTIVE_PER_OWNER = 8
MAX_ATTEMPTS = 3
LEASE_SECONDS = 90


class BrowserJobError(ValueError):
    """Static error reason; never include user URL, secrets or page metadata."""


class BrowserInspectionJob(Base):
    __tablename__ = "browser_inspection_jobs"
    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_origin: Mapped[str] = mapped_column(String(253), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    lease_expires_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    page_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    blocked_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    __table_args__ = (UniqueConstraint("subject", "request_key", name="uq_browser_owner_request"),)


def _subject(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        raise BrowserJobError("verified_subject_required")
    return value


def _id(value: str) -> str:
    if not isinstance(value, str) or JOB_ID.fullmatch(value) is None:
        raise BrowserJobError("job_id_invalid")
    return value


def _url(url: str, origins: tuple[str, ...] | list[str]) -> str:
    """Constrain browser jobs to clean, explicitly allowed HTTPS paths.

    Query strings are denied in the queued interface to avoid persisting
    URLs with tokens, credentials or personal search data in plaintext.
    """
    if not isinstance(url, str) or len(url) > 1500:
        raise BrowserJobError("browser_url_invalid")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise BrowserJobError("browser_url_invalid") from None
    if parsed.query or parsed.fragment or ";" in parsed.path:
        raise BrowserJobError("browser_url_query_forbidden")
    try:
        return BrowserPolicy.from_origins(origins).require(url)
    except BrowserPolicyError:
        raise BrowserJobError("browser_origin_not_permitted") from None


@dataclass(frozen=True)
class BrowserJobQueue:
    vault: Vault

    def submit(self, subject: str, url: str, request_key: str,
               allowed_origins: tuple[str, ...] | list[str]) -> dict:
        _subject(subject)
        if not isinstance(request_key, str) or KEY.fullmatch(request_key) is None:
            raise BrowserJobError("idempotency_key_invalid")
        origin = _url(url, allowed_origins)
        now = time.time()
        with self.vault.sessions.begin() as session:
            existing = session.execute(select(BrowserInspectionJob).where(
                BrowserInspectionJob.subject == subject,
                BrowserInspectionJob.request_key == request_key,
            )).scalar_one_or_none()
            if existing is not None:
                if existing.requested_url != url or existing.allowed_origin != origin:
                    raise BrowserJobError("idempotency_key_conflict")
                return {"status": "EXISTING", "job_id": existing.job_id,
                        "job_state": existing.state}
            active = session.execute(select(func.count()).select_from(BrowserInspectionJob).where(
                BrowserInspectionJob.subject == subject,
                BrowserInspectionJob.state.in_(("PENDING", "RUNNING")),
            )).scalar_one()
            if active >= MAX_ACTIVE_PER_OWNER:
                raise BrowserJobError("too_many_active_browser_jobs")
            job_id = secrets.token_hex(16)
            session.add(BrowserInspectionJob(
                job_id=job_id, subject=subject, request_key=request_key,
                requested_url=url, allowed_origin=origin, state="PENDING",
                attempts=0, created_at=now, updated_at=now,
            ))
        return {"status": "CREATED", "job_id": job_id, "job_state": "PENDING",
                "browser_executed": False}

    def status(self, subject: str, job_id: str) -> dict:
        _subject(subject)
        _id(job_id)
        with self.vault.sessions() as session:
            row = session.execute(select(BrowserInspectionJob).where(
                BrowserInspectionJob.subject == subject,
                BrowserInspectionJob.job_id == job_id,
            )).scalar_one_or_none()
            if row is None:
                raise BrowserJobError("browser_job_not_found")
            response = {"status": row.state, "job_id": row.job_id,
                        "untrusted_page_metadata": True,
                        "attempts": row.attempts,
                        "origin": row.allowed_origin,
                        "browser_executed": row.state == "COMPLETED",
                        "read_only": True, "login_supported": False}
            if row.state == "COMPLETED":
                response["title"] = row.page_title
                response["blocked_requests"] = row.blocked_requests
            if row.state == "FAILED":
                response["failure_code"] = row.failure_code
            return response

    def cancel(self, subject: str, job_id: str) -> dict:
        _subject(subject)
        _id(job_id)
        with self.vault.sessions.begin() as session:
            row = session.execute(select(BrowserInspectionJob).where(
                BrowserInspectionJob.subject == subject,
                BrowserInspectionJob.job_id == job_id,
            ).with_for_update()).scalar_one_or_none()
            if row is None:
                raise BrowserJobError("browser_job_not_found")
            if row.state not in ("PENDING", "RUNNING"):
                return {"status": row.state, "job_id": job_id}
            row.state = "CANCELLED"
            row.lease_hash = ""
            row.lease_expires_at = 0.0
            row.updated_at = time.time()
        return {"status": "CANCELLED", "job_id": job_id}

    def claim(self, allowed_origins: tuple[str, ...] | list[str]) -> dict | None:
        """Trusted worker only; Postgres SKIP LOCKED coordinates worker replicas."""
        policy = BrowserPolicy.from_origins(allowed_origins)
        now = time.time()
        with self.vault.sessions.begin() as session:
            rows = session.execute(select(BrowserInspectionJob).where(
                (BrowserInspectionJob.state == "PENDING") |
                ((BrowserInspectionJob.state == "RUNNING") &
                 (BrowserInspectionJob.lease_expires_at < now)),
            ).order_by(BrowserInspectionJob.created_at, BrowserInspectionJob.job_id)
             .limit(50).with_for_update(skip_locked=True)).scalars().all()
            for row in rows:
                if row.attempts >= MAX_ATTEMPTS:
                    row.state = "FAILED"
                    row.failure_code = "retry_budget_exhausted"
                    row.lease_hash = ""
                    row.updated_at = now
                    continue
                try:
                    # Enforce CURRENT worker allowlist, not the policy at submit time.
                    policy.require(row.requested_url)
                    if row.allowed_origin not in policy.allowed_origins:
                        raise BrowserPolicyError("origin_not_allowed")
                except BrowserPolicyError:
                    row.state = "FAILED"
                    row.failure_code = "worker_origin_policy_denied"
                    row.updated_at = now
                    continue
                token = secrets.token_urlsafe(32)
                row.state = "RUNNING"
                row.attempts += 1
                row.lease_hash = hashlib.sha256(token.encode()).hexdigest()
                row.lease_expires_at = now + LEASE_SECONDS
                row.updated_at = now
                return {"job_id": row.job_id, "url": row.requested_url,
                        "origin": row.allowed_origin, "lease_token": token}
        return None

    def finish(self, job_id: str, lease_token: str, result: dict | None,
               *, failed: bool = False) -> dict:
        """Trusted worker commit: a stale/cancelled lease cannot publish output."""
        _id(job_id)
        if not isinstance(lease_token, str) or not 30 <= len(lease_token) <= 128:
            raise BrowserJobError("browser_lease_invalid")
        now = time.time()
        with self.vault.sessions.begin() as session:
            row = session.execute(select(BrowserInspectionJob).where(
                BrowserInspectionJob.job_id == job_id,
            ).with_for_update()).scalar_one_or_none()
            if (row is None or row.state != "RUNNING"
                    or row.lease_expires_at <= now
                    or not hmac.compare_digest(
                        row.lease_hash, hashlib.sha256(lease_token.encode()).hexdigest()
                    )):
                raise BrowserJobError("browser_lease_expired_or_cancelled")
            row.lease_hash = ""
            row.lease_expires_at = 0.0
            row.updated_at = now
            if failed:
                row.state = "FAILED" if row.attempts >= MAX_ATTEMPTS else "PENDING"
                row.failure_code = "inspection_failed" if row.state == "FAILED" else ""
                return {"status": row.state}
            if (not isinstance(result, dict) or result.get("status") != "INSPECTED"
                    or result.get("origin") != row.allowed_origin
                    or not isinstance(result.get("title"), str)
                    or not isinstance(result.get("blocked_requests"), int)
                    or not 0 <= result["blocked_requests"] <= 100000):
                raise BrowserJobError("browser_worker_result_invalid")
            # Page titles are adversarial input; remove terminal/control
            # characters and never interpret them as instructions or markup.
            row.page_title = "".join(
                char for char in result["title"]
                if char.isprintable() and char not in {"\u2028", "\u2029"}
            )[:200]
            row.blocked_requests = result["blocked_requests"]
            row.state = "COMPLETED"
            return {"status": "COMPLETED"}
