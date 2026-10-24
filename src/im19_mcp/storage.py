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

from cryptography.fernet import Fernet
from sqlalchemy import Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


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
    def __init__(self, database_url: str, *, encryption_key: str, audit_salt: str = ""):
        if not encryption_key:
            raise ValueError("encryption_key_required")
        self.cipher = Fernet(encryption_key.encode("ascii"))
        self.audit_salt = audit_salt.encode("utf-8")
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def encrypt(self, value: object) -> str:
        return self.cipher.encrypt(json.dumps(value, separators=(",", ":")).encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> object:
        return json.loads(self.cipher.decrypt(ciphertext.encode("ascii")))

    def create_state(self, state: str, subject: str, scopes: tuple[str, ...], verifier: str, expires: float) -> None:
        hashed = hashlib.sha256(state.encode("ascii")).hexdigest()
        with self.sessions.begin() as session:
            session.add(PendingState(
                state_hash=hashed, subject=subject, scopes_json=json.dumps(scopes),
                verifier_ciphertext=self.encrypt(verifier), expires_at=expires,
            ))

    def consume_state(self, state: str) -> tuple[str, tuple[str, ...], str]:
        hashed = hashlib.sha256(state.encode("ascii")).hexdigest()
        with self.sessions.begin() as session:
            row = session.execute(
                select(PendingState).where(PendingState.state_hash == hashed).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise PermissionError("oauth_state_unknown_or_reused")
            session.delete(row)
            if row.expires_at <= time.time():
                raise PermissionError("oauth_state_expired")
            return row.subject, tuple(json.loads(row.scopes_json)), str(self.decrypt(row.verifier_ciphertext))

    def put_credential(self, subject: str, service: str, account_id: str, token: dict, expires: float) -> None:
        with self.sessions.begin() as session:
            row = session.get(Credential, (subject, service))
            if row is None:
                row = Credential(subject=subject, service=service, token_ciphertext="",
                                 account_id=account_id, expires_at=expires)
                session.add(row)
            if row.account_id != account_id:
                raise PermissionError("account_rebind_requires_disconnect")
            row.token_ciphertext = self.encrypt(token)
            row.expires_at = expires

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

    def audit(self, subject: str, service: str, action: str, status: str) -> None:
        pseudonym = hmac.new(self.audit_salt, subject.encode("utf-8"), hashlib.sha256).hexdigest()
        with self.sessions.begin() as session:
            session.add(AuditEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                subject_pseudonym=pseudonym, service=service, action=action,
                status=status, request_id=secrets.token_hex(16),
            ))
