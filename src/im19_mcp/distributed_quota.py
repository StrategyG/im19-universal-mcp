"""Database-backed fixed-window quotas for remote MCP traffic.

Designed for PostgreSQL. Atomic UPSERT with conditional UPDATE prevents the
classic SELECT/increment race across multiple gateway workers. SQLite is used
only for local/CI tests. Fixed windows can burst across a boundary; deploy a
trusted edge/WAF limiter as an additional control.

Do not accept an unauthenticated subject or X-Forwarded-For as a quota identity.
Call with ASGI socket peer IP for pre-auth limits, or MCP-verified subject after
issuer/audience/scope verification.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Final

from sqlalchemy import BigInteger, Engine, Integer, String, delete, text
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from im19_mcp.storage import Base

_MAX_WINDOW_SECONDS: Final[int] = 86400
_MAX_LIMIT: Final[int] = 100000
_MAX_ID_LEN: Final[int] = 512

# Parameterized SQL avoids a read-modify-write race on simultaneous clients.
# PostgreSQL and SQLite >= 3.35 both implement ON CONFLICT + RETURNING.
_ATOMIC_QUOTA = text("""
INSERT INTO distributed_rate_windows
    (key_digest, bucket_start, hits, expires_at)
VALUES (:digest, :bucket, 1, :expires)
ON CONFLICT (key_digest, bucket_start)
DO UPDATE SET hits = distributed_rate_windows.hits + 1
WHERE distributed_rate_windows.hits < :max_hits
RETURNING hits
""")


class RateWindow(Base):
    __tablename__ = "distributed_rate_windows"

    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket_start: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class QuotaUnavailable(RuntimeError):
    """Unable to enforce a shared quota; gateway must fail closed."""


class DistributedQuota:
    """Pseudonymized persistent quotas with strict atomic per-window admission.

    This class does not authenticate callers. Only trusted gateway code
    decides the identity and namespace. The HMAC key is the separate audit
    salt and must never be exposed through provider or model tools.
    """

    def __init__(self, engine: Engine, *, secret: str):
        if not isinstance(secret, str) or len(secret) < 16:
            raise ValueError("rate_quota_secret_too_short")
        self.engine = engine
        self._secret = secret.encode("utf-8")
        self.sessions = sessionmaker(engine)

    def _digest(self, namespace: str, identity: str, window_seconds: int) -> str:
        if (not isinstance(namespace, str) or not 1 <= len(namespace) <= 96 or
                not isinstance(identity, str) or not 1 <= len(identity) <= _MAX_ID_LEN):
            raise ValueError("quota_identity_invalid")
        key = f"{namespace}\0{window_seconds}\0{identity}".encode("utf-8")
        return hmac.new(self._secret, key, hashlib.sha256).hexdigest()

    def consume(
        self, namespace: str, identity: str, *, limit: int,
        window_seconds: int = 60, now: float | None = None,
    ) -> tuple[bool, int]:
        """Reserve one admission; return (admitted, seconds until next window).

        Failures in the database are raised, never silently treated as
        an allowance. Callers MUST return 503 / SERVICE_UNAVAILABLE.
        """
        if (isinstance(limit, bool) or not isinstance(limit, int) or
                not 1 <= limit <= _MAX_LIMIT or isinstance(window_seconds, bool) or
                not isinstance(window_seconds, int) or
                not 1 <= window_seconds <= _MAX_WINDOW_SECONDS):
            raise ValueError("invalid_quota_parameters")
        digest = self._digest(namespace, identity, window_seconds)
        timestamp = time.time() if now is None else now
        if not isinstance(timestamp, (int, float)) or timestamp < 0:
            raise ValueError("invalid_quota_time")
        # An absolute bucket avoids local-worker state. NTP-synchronized clocks
        # are required across gateway nodes; the DB ensures atomic increments.
        bucket = int(timestamp // window_seconds)
        retry = max(1, min(window_seconds, int((bucket + 1) * window_seconds - timestamp + 0.99999)))
        try:
            with self.sessions.begin() as session:
                row = session.execute(
                    _ATOMIC_QUOTA,
                    {"digest": digest, "bucket": bucket, "expires": int((bucket + 2) * window_seconds),
                     "max_hits": limit},
                ).one_or_none()
        except Exception as exc:
            raise QuotaUnavailable("distributed_quota_storage_unavailable") from exc
        return row is not None, retry

    def purge_expired(self, *, now: float | None = None) -> int:
        """Trusted maintenance command; never called from public MCP tools."""
        timestamp = time.time() if now is None else now
        with self.sessions.begin() as session:
            result = session.execute(delete(RateWindow).where(RateWindow.expires_at < int(timestamp)))
            return max(int(result.rowcount or 0), 0)
