"""Persistent one-time human approval and fail-closed mutation reservations.

Only trusted, independently authenticated backend code may call issue().
No approval creation or mutation execution tool is exposed to the model.

A reservation is NOT an external API execution. A crashed/uncertain execution
cannot be automatically retried; reconcile with the provider before recovery.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Float, String, UniqueConstraint, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from im19_mcp.storage import AuditEvent, Base, Vault

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_STATUSES = frozenset({"SUCCEEDED", "FAILED_RECONCILIATION_REQUIRED"})


class ApprovalDenied(PermissionError):
    """Approval is missing, expired, incorrectly scoped, or already consumed."""


class DuplicateExecution(RuntimeError):
    """A given idempotency key has already been reserved or processed."""


class ApprovalGrant(Base):
    __tablename__ = "governance_approvals"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approver: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class MutationReceipt(Base):
    __tablename__ = "governance_mutation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "subject", "provider", "account_id", "action", "idempotency_key",
            name="uq_governance_mutation_identity",
        ),
    )

    reservation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


@dataclass(frozen=True)
class ExecutionReservation:
    reservation_id: str
    status: str
    idempotency_key: str
    payload_hash: str


def payload_sha256(payload: dict[str, Any]) -> str:
    """Hash canonical JSON; reject NaN, non-string keys and non-JSON data."""
    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_json_object")
    def string_keys(value):
        if isinstance(value, dict):
            return all(isinstance(key, str) and string_keys(item) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return all(string_keys(item) for item in value)
        return True

    if not string_keys(payload):
        raise ValueError("payload_keys_must_be_strings")
    try:
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("payload_not_canonical_json") from exc
    if len(raw) > 64 * 1024:
        raise ValueError("payload_too_large")
    return hashlib.sha256(raw).hexdigest()


def _identity(subject: str, provider: str, account_id: str, action: str, resource_id: str) -> None:
    values = (subject, provider, account_id, action, resource_id)
    lengths = (255, 64, 255, 128, 512)
    if any(not isinstance(v, str) or not v or len(v) > limit for v, limit in zip(values, lengths)):
        raise ValueError("approval_identity_invalid")


class ApprovalLedger:
    """Storage-layer governance; trusted portal must verify human before issue().

    The ledger NEVER invokes external provider APIs itself.
    """

    def __init__(self, vault: Vault):
        if not vault.audit_salt:
            raise ValueError("audit_salt_required")
        self.vault = vault

    def _audit(self, session: Session, subject: str, provider: str,
               action: str, status: str) -> None:
        pseudo = hmac.new(
            self.vault.audit_salt, subject.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        session.add(AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            subject_pseudonym=pseudo, service=provider,
            action=action, status=status, request_id=secrets.token_hex(16),
        ))

    def issue(
        self, *, subject: str, provider: str, account_id: str, action: str,
        resource_id: str, payload_hash: str, approver: str,
        evidence_reference: str, ttl_seconds: int = 300,
        four_eyes: bool = False,
    ) -> str:
        """Issue an approval ONLY after trusted portal human verification.

        The caller must provide an independently verified approver identity
        and evidence reference. Not safe to expose this method over MCP.
        """
        _identity(subject, provider, account_id, action, resource_id)
        if not _SHA256.fullmatch(payload_hash):
            raise ValueError("payload_hash_invalid")
        if (not isinstance(approver, str) or not approver or len(approver) > 255 or
                not isinstance(evidence_reference, str) or
                not 8 <= len(evidence_reference) <= 128 or
                not 30 <= ttl_seconds <= 600):
            raise ValueError("approval_evidence_or_ttl_invalid")
        if four_eyes and hmac.compare_digest(subject, approver):
            raise ApprovalDenied("four_eyes_requires_distinct_approver")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self.vault.sessions.begin() as session:
            session.add(ApprovalGrant(
                code_hash=digest, subject=subject, provider=provider,
                account_id=account_id, action=action, resource_id=resource_id,
                payload_hash=payload_hash, approver=approver,
                evidence_reference=evidence_reference,
                expires_at=time.time() + ttl_seconds,
            ))
            self._audit(session, subject, provider, "governance.issue", "ISSUED")
        return token

    def reserve(
        self, *, token: str, subject: str, provider: str, account_id: str,
        action: str, resource_id: str, payload: dict[str, Any],
        idempotency_key: str,
    ) -> ExecutionReservation:
        """Atomically consume approval and reserve idempotency key.

        No external write is allowed before this returns. Reusing a key,
        even with a new approval token, NEVER authorizes a second write.
        """
        _identity(subject, provider, account_id, action, resource_id)
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            raise ApprovalDenied("approval_invalid_or_expired")
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY.fullmatch(idempotency_key):
            raise ValueError("idempotency_key_invalid")
        digest = payload_sha256(payload)
        code_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        reservation_id = secrets.token_hex(24)
        now = time.time()
        with self.vault.sessions() as session:
            previous = session.execute(select(MutationReceipt).where(
                MutationReceipt.subject == subject,
                MutationReceipt.provider == provider,
                MutationReceipt.account_id == account_id,
                MutationReceipt.action == action,
                MutationReceipt.idempotency_key == idempotency_key,
            )).scalar_one_or_none()
            if previous is not None:
                raise DuplicateExecution("mutation_already_reserved_no_automatic_retry")
        try:
            with self.vault.sessions.begin() as session:
                row = session.execute(
                    delete(ApprovalGrant)
                    .where(
                        ApprovalGrant.code_hash == code_hash,
                        ApprovalGrant.subject == subject,
                        ApprovalGrant.provider == provider,
                        ApprovalGrant.account_id == account_id,
                        ApprovalGrant.action == action,
                        ApprovalGrant.resource_id == resource_id,
                        ApprovalGrant.payload_hash == digest,
                    )
                    .returning(ApprovalGrant.expires_at)
                ).one_or_none()
                if row is None or row.expires_at <= now:
                    raise ApprovalDenied("approval_invalid_or_expired")
                session.add(MutationReceipt(
                    reservation_id=reservation_id, subject=subject,
                    provider=provider, account_id=account_id, action=action,
                    resource_id=resource_id, payload_hash=digest,
                    idempotency_key=idempotency_key, approval_hash=code_hash,
                    status="RESERVED", created_at=now, finished_at=None,
                ))
                # Flush before auditing; a unique-key conflict rolls back BOTH
                # receipt insertion and approval consumption.
                session.flush()
                self._audit(session, subject, provider, "governance.reserve", "RESERVED")
        except IntegrityError as exc:
            raise DuplicateExecution("mutation_already_reserved_no_automatic_retry") from exc
        return ExecutionReservation(reservation_id, "RESERVED", idempotency_key, digest)

    def finalize(
        self, *, reservation_id: str, subject: str, outcome: str,
    ) -> bool:
        """Only mark known results; uncertain outcomes remain RESERVED.

        A failed provider write is NOT automatically retriable. Inspect the
        provider first; an operator can resolve the uncertainty separately.
        """
        if outcome not in _STATUSES:
            raise ValueError("mutation_outcome_invalid")
        if not subject or not isinstance(reservation_id, str):
            raise ValueError("mutation_identity_invalid")
        with self.vault.sessions.begin() as session:
            updated = session.execute(
                update(MutationReceipt)
                .where(
                    MutationReceipt.reservation_id == reservation_id,
                    MutationReceipt.subject == subject,
                    MutationReceipt.status == "RESERVED",
                )
                .values(status=outcome, finished_at=time.time())
                .returning(MutationReceipt.provider)
            ).one_or_none()
            if updated is None:
                return False
            self._audit(session, subject, updated.provider, "governance.finalize", outcome)
            return True

    def get_receipt(self, *, subject: str, reservation_id: str) -> ExecutionReservation | None:
        """Only return metadata for a receipt belonging to the same subject."""
        with self.vault.sessions() as session:
            record = session.execute(select(MutationReceipt).where(
                MutationReceipt.reservation_id == reservation_id,
                MutationReceipt.subject == subject,
            )).scalar_one_or_none()
            if record is None:
                return None
            return ExecutionReservation(
                record.reservation_id, record.status,
                record.idempotency_key, record.payload_hash,
            )

    def purge_expired_approvals(self, *, now: float | None = None) -> int:
        """Trusted retention job; pending unused approvals expire fail-closed."""
        cutoff = time.time() if now is None else now
        with self.vault.sessions.begin() as session:
            result = session.execute(
                delete(ApprovalGrant).where(ApprovalGrant.expires_at <= cutoff)
            )
            return max(int(result.rowcount or 0), 0)

