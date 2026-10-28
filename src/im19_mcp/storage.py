"""SQLAlchemy storage, encrypted credentials and one-time OAuth state.

Production must use PostgreSQL and externally managed Fernet encryption keys.
Schema creation is explicit, never automatic on unauthenticated requests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet, MultiFernet
from cryptography.fernet import InvalidToken
from sqlalchemy import Float, Integer, String, Text, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from im19_mcp.database import database_engine


class Base(DeclarativeBase):
    pass


class PendingState(Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    verifier_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class Credential(Base):
    __tablename__ = "oauth_credentials"
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    service: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    refresh_owner: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    refresh_until: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")


class PendingLink(Base):
    """Provider credential staged for explicit authenticated MCP confirmation.

    This table never contains raw confirmation codes or plaintext OAuth tokens.
    """
    __tablename__ = "oauth_pending_links"
    confirmation_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_pseudonym: Mapped[str] = mapped_column(String(64), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)


class Vault:
    def __init__(
        self, database_url: str, *, encryption_key: str, audit_salt: str = "",
        previous_keys: tuple[str, ...] = (),
    ):
        if not encryption_key:
            raise ValueError("encryption_key_required")
        if encryption_key in previous_keys or len(previous_keys) != len(set(previous_keys)):
            raise ValueError("duplicate_encryption_keys_forbidden")
        try:
            primary = Fernet(encryption_key.encode("ascii"))
            self._primary_cipher = primary
            self._has_previous_keys = bool(previous_keys)
            # Keep the single-key Fernet object for backward compatibility.
            # MultiFernet always ENCRYPTS with the first (new) key.
            self.cipher = (
                MultiFernet([primary] + [Fernet(k.encode("ascii")) for k in previous_keys])
                if previous_keys else primary
            )
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("fernet_key_invalid") from exc
        self.audit_salt = audit_salt.encode("utf-8")
        self.engine = database_engine(database_url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize_schema(self) -> None:
        if self.engine.dialect.name != "sqlite":
            raise PermissionError("local_schema_bootstrap_requires_sqlite_use_alembic")
        # Import optional governance models before local-dev create_all.
        # Production schema changes still require explicit Alembic migrations.
        from im19_mcp.governance import approvals  # noqa: F401
        from im19_mcp import distributed_quota  # noqa: F401
        from im19_mcp.governance import write_approval  # noqa: F401
        from im19_mcp import browser_jobs  # noqa: F401

        Base.metadata.create_all(self.engine)

    def encrypt(self, value: object) -> str:
        return self.cipher.encrypt(json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> object:
        return json.loads(self.cipher.decrypt(ciphertext.encode("ascii")))

    def verify_primary_key_coverage(self) -> dict[str, int]:
        """Fail closed if any sensitive row still requires an old encryption key.

        Run after offline rotation, before removing previous keys from service
        configuration. Uses only the new primary key, never the fallback ring.
        """
        counts: dict[str, int] = {}
        with self.sessions() as session:
            for model, attr, label in (
                (Credential, Credential.token_ciphertext, "oauth_credentials"),
                (PendingState, PendingState.verifier_ciphertext, "oauth_states"),
                (PendingLink, PendingLink.token_ciphertext, "oauth_pending_links"),
            ):
                tokens = session.execute(select(attr).select_from(model)).scalars()
                count = 0
                for token in tokens:
                    try:
                        self._primary_cipher.decrypt(token.encode("ascii"))
                    except (InvalidToken, UnicodeError) as exc:
                        raise ValueError("primary_key_coverage_failed") from exc
                    count += 1
                counts[label] = count
        return counts

    def rotate_encryption(self, *, offline_maintenance_ack: bool = False) -> dict[str, int]:
        """Offline, all-or-nothing re-encryption of every sensitive database field.

        A full database transaction ensures that a bad ciphertext rolls back
        previously visited records. PostgreSQL SELECT FOR UPDATE protects rows
        from concurrent token changes, but callers MUST stop all writer
        processes and back up the DB. No live zero-downtime guarantee.
        """
        if not offline_maintenance_ack:
            raise PermissionError("offline_maintenance_ack_required")
        if not self._has_previous_keys:
            raise ValueError("previous_key_required_for_rotation")
        if not isinstance(self.cipher, MultiFernet):
            raise ValueError("rotation_cipher_invalid")
        counters: dict[str, int] = {}
        try:
            with self.sessions.begin() as session:
                for model, field, name in (
                    (Credential, "token_ciphertext", "oauth_credentials"),
                    (PendingState, "verifier_ciphertext", "oauth_states"),
                    (PendingLink, "token_ciphertext", "oauth_pending_links"),
                ):
                    rows = session.execute(select(model).with_for_update()).scalars().all()
                    for row in rows:
                        original = getattr(row, field)
                        replaced = self.cipher.rotate(original.encode("ascii")).decode("ascii")
                        setattr(row, field, replaced)
                    session.flush()
                    counters[name] = len(rows)
        except (InvalidToken, UnicodeError) as exc:
            raise ValueError("rotation_decryption_failed_database_unchanged") from exc
        # Verify outside the write transaction. The maintenance run must be
        # offline to guarantee nobody creates old-key rows after rotation.
        self.verify_primary_key_coverage()
        return counters

    def create_state(
        self, state: str, subject: str, scopes: tuple[str, ...], verifier: str,
        expires: float, *, service: str = "youtube", browser_hash: str = "",
        purpose: str = "google",
    ) -> None:
        """Create an opaque state bound to an MCP principal and provider service."""
        if service not in {"youtube", "drive"} or not subject or not verifier:
            raise ValueError("oauth_state_binding_invalid")
        if purpose not in {"google", "browser-ticket", "browser-login"}:
            raise ValueError("oauth_state_purpose_invalid")
        hashed = hashlib.sha256(state.encode("ascii")).hexdigest()
        with self.sessions.begin() as session:
            session.add(PendingState(
                state_hash=hashed, subject=subject,
                scopes_json=json.dumps({"scopes": scopes, "service": service,
                                       "browser_hash": browser_hash, "purpose": purpose}),
                verifier_ciphertext=self.encrypt(verifier), expires_at=expires,
            ))

    def consume_state(self, state: str, *, browser_hash: str = "",
                      purpose: str = "google") -> tuple[str, str, tuple[str, ...], str]:
        """Atomically remove state; concurrent callbacks cannot redeem it twice.

        DELETE RETURNING is supported by PostgreSQL and modern SQLite (>=3.35).
        Expired states remain consumed, including on the denial path.
        """
        try:
            hashed = hashlib.sha256(state.encode("ascii")).hexdigest()
        except UnicodeError:
            raise PermissionError("oauth_state_invalid") from None
        stmt = (
            delete(PendingState)
            .where(PendingState.state_hash == hashed)
            .returning(
                PendingState.subject, PendingState.scopes_json,
                PendingState.verifier_ciphertext, PendingState.expires_at,
            )
        )
        with self.sessions.begin() as session:
            row = session.execute(stmt).one_or_none()
        if row is None:
            raise PermissionError("oauth_state_unknown_or_reused")
        if row.expires_at <= time.time():
            raise PermissionError("oauth_state_expired")
        binding = json.loads(row.scopes_json)
        if not isinstance(binding, dict) or binding.get("service") not in {"youtube", "drive"}:
            raise PermissionError("oauth_state_binding_invalid")
        if (binding.get("purpose", "google") != purpose or
                not hmac.compare_digest(binding.get("browser_hash", ""), browser_hash)):
            raise PermissionError("oauth_browser_binding_invalid")
        scopes = binding.get("scopes")
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            raise PermissionError("oauth_state_scope_invalid")
        return row.subject, binding["service"], tuple(scopes), str(self.decrypt(row.verifier_ciphertext))

    def stage_link(
        self, subject: str, service: str, account_id: str,
        token: dict, token_expires_at: float, *, ttl_seconds: int = 300
    ) -> str:
        """Encrypt provider data; return a short-lived code shown in browser only."""
        if not subject or service not in {"youtube", "drive"} or not account_id:
            raise ValueError("invalid_pending_link")
        if not 30 <= ttl_seconds <= 600 or token_expires_at <= time.time():
            raise ValueError("invalid_link_expiration")
        code = secrets.token_urlsafe(32)
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()
        with self.sessions.begin() as session:
            session.add(PendingLink(
                confirmation_hash=digest, subject=subject, service=service,
                account_id=account_id, token_ciphertext=self.encrypt(token),
                token_expires_at=token_expires_at,
                expires_at=time.time() + ttl_seconds,
            ))
        return code

    def confirm_link(self, subject: str, service: str, confirmation_code: str) -> tuple[str, dict, float]:
        """Consume exact user's one-time claim and return staged encrypted credentials.

        A wrong-user attempt does NOT invalidate another user's claim.
        Atomic DELETE RETURNING prevents any claim from being confirmed twice.
        """
        if (not isinstance(confirmation_code, str) or
                not 20 <= len(confirmation_code) <= 256 or
                not subject or service not in {"youtube", "drive"}):
            raise PermissionError("confirmation_invalid_or_expired")
        digest = hashlib.sha256(confirmation_code.encode("utf-8")).hexdigest()
        stmt = (
            delete(PendingLink)
            .where(PendingLink.confirmation_hash == digest,
                   PendingLink.subject == subject,
                   PendingLink.service == service)
            .returning(PendingLink.account_id, PendingLink.token_ciphertext,
                       PendingLink.token_expires_at, PendingLink.expires_at)
        )
        with self.sessions.begin() as session:
            row = session.execute(stmt).one_or_none()
        if row is None or row.expires_at <= time.time() or row.token_expires_at <= time.time():
            raise PermissionError("confirmation_invalid_or_expired")
        data = self.decrypt(row.token_ciphertext)
        if not isinstance(data, dict):
            raise PermissionError("confirmation_invalid_or_expired")
        return row.account_id, data, row.token_expires_at

    def put_credential(self, subject: str, service: str, account_id: str, token: dict, expires: float) -> None:
        ciphertext = self.encrypt(token)
        with self.sessions.begin() as session:
            self._put_credential(session, subject, service, account_id, ciphertext, expires)

    def _put_credential(self, session, subject, service, account_id, ciphertext, expires):
        # First-time concurrent confirmations must obey the same no-rebind
        # rule as updates. A SELECT followed by INSERT is insufficient.
        if self.engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif self.engine.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise ValueError("unsupported_vault_database")
        values = {"subject": subject, "service": service, "account_id": account_id,
                  "token_ciphertext": ciphertext, "expires_at": expires,
                  "refresh_owner": "", "refresh_until": 0.0}
        stmt = insert(Credential).values(**values).on_conflict_do_update(
            index_elements=[Credential.subject, Credential.service],
            set_={key: value for key, value in values.items() if key not in {"subject", "service"}},
            where=Credential.account_id == account_id,
        ).returning(Credential.account_id)
        if session.execute(stmt).scalar_one_or_none() is None:
            raise PermissionError("account_rebind_requires_disconnect")

    def activate_link(self, subject: str, service: str, code: str, *,
                      require_browser: bool = False) -> tuple[str, dict, float]:
        """Claim consumption, no-rebind activation and success audit commit together."""
        if not isinstance(code, str) or not 20 <= len(code) <= 256:
            raise PermissionError("confirmation_invalid_or_expired")
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        with self.sessions.begin() as session:
            row = session.execute(
                delete(PendingLink).where(PendingLink.confirmation_hash == digest,
                    PendingLink.subject == subject, PendingLink.service == service)
                .returning(PendingLink.account_id, PendingLink.token_ciphertext,
                           PendingLink.token_expires_at, PendingLink.expires_at)
            ).one_or_none()
            if row is None or min(row.expires_at, row.token_expires_at) <= time.time():
                result = None
            else:
                data = self.decrypt(row.token_ciphertext)
                if not isinstance(data, dict):
                    raise PermissionError("confirmation_invalid_or_expired")
                if require_browser and data.get("browser_verified") is not True:
                    raise PermissionError("browser_proof_required")
                self._put_credential(session, subject, service, row.account_id,
                                     row.token_ciphertext, row.token_expires_at)
                session.add(self._audit_record(subject, service, "oauth.confirm", "SUCCESS"))
                result = (row.account_id, data, row.token_expires_at)
        # Invalid/expired claims remain consumed, but a transaction failure
        # leaves a valid claim retriable and no half-activated credential.
        if result is None:
            raise PermissionError("confirmation_invalid_or_expired")
        return result

    def acquire_refresh_lease(
        self, subject: str, service: str, owner: str, *,
        now: float, lease_seconds: int = 30
    ) -> tuple[str, dict, float] | None:
        """Lease a specific account's refresh across workers using SQL UPDATE.

        Leases use DB time parameters and atomically compare expiration. The
        provider request takes place *outside* the transaction. Replaced or
        revoked credentials cannot be resurrected by stale workers.
        """
        if not owner or not 10 <= lease_seconds <= 120:
            raise ValueError("refresh_lease_invalid")
        stmt = (
            update(Credential)
            .where(
                Credential.subject == subject,
                Credential.service == service,
                Credential.refresh_until <= now,
            )
            .values(refresh_owner=owner, refresh_until=now + lease_seconds)
            .returning(Credential.account_id, Credential.token_ciphertext,
                       Credential.expires_at)
        )
        with self.sessions.begin() as session:
            row = session.execute(stmt).one_or_none()
        if row is None:
            return None
        data = self.decrypt(row.token_ciphertext)
        if not isinstance(data, dict):
            raise ValueError("token_data_invalid")
        return row.account_id, data, row.expires_at

    def complete_refresh(
        self, subject: str, service: str, owner: str, account_id: str,
        token: dict, expires: float
    ) -> bool:
        """Only the still-current lease owner may persist replacement tokens."""
        stmt = (
            update(Credential)
            .where(
                Credential.subject == subject,
                Credential.service == service,
                Credential.account_id == account_id,
                Credential.refresh_owner == owner,
            )
            .values(
                token_ciphertext=self.encrypt(token),
                expires_at=expires,
                refresh_owner="",
                refresh_until=0.0,
            )
        )
        with self.sessions.begin() as session:
            result = session.execute(stmt)
            return bool(result.rowcount)

    def release_refresh_lease(self, subject: str, service: str, owner: str) -> None:
        """Release only the caller's current lease, never another worker's."""
        stmt = (
            update(Credential)
            .where(
                Credential.subject == subject,
                Credential.service == service,
                Credential.refresh_owner == owner,
            )
            .values(refresh_owner="", refresh_until=0.0)
        )
        with self.sessions.begin() as session:
            session.execute(stmt)

    def get_credential(self, subject: str, service: str) -> tuple[str, dict, float] | None:
        with self.sessions() as session:
            row = session.get(Credential, (subject, service))
            if row is None:
                return None
            result = self.decrypt(row.token_ciphertext)
            if not isinstance(result, dict):
                raise ValueError("token_data_invalid")
            return row.account_id, result, row.expires_at

    def delete_credential(self, subject: str, service: str) -> bool:
        with self.sessions.begin() as session:
            row = session.get(Credential, (subject, service))
            if row is None:
                return False
            session.delete(row)
            return True

    def purge_expired(self, *, now: float | None = None) -> dict[str, int]:
        """Delete obsolete OAuth transactions and encrypted pending credentials.

        Call from a scheduled *trusted backend* task, never via public MCP.
        Encrypted pending tokens otherwise remain in the database past TTL.
        """
        cutoff = time.time() if now is None else now
        with self.sessions.begin() as session:
            states = session.execute(
                delete(PendingState).where(PendingState.expires_at <= cutoff)
            )
            links = session.execute(
                delete(PendingLink).where(
                    (PendingLink.expires_at <= cutoff) |
                    (PendingLink.token_expires_at <= cutoff)
                )
            )
            return {
                "oauth_states": max(int(states.rowcount or 0), 0),
                "oauth_pending_links": max(int(links.rowcount or 0), 0),
            }

    def audit(self, subject: str, service: str, action: str, status: str) -> None:
        with self.sessions.begin() as session:
            session.add(self._audit_record(subject, service, action, status))

    def _audit_record(self, subject: str, service: str, action: str, status: str) -> AuditEvent:
        pseudonym = hmac.new(self.audit_salt, subject.encode("utf-8"), hashlib.sha256).hexdigest()
        return AuditEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                subject_pseudonym=pseudonym, service=service, action=action,
                status=status, request_id=secrets.token_hex(16),
            )

