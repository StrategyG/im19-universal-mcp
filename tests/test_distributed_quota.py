"""Distributed shared quota security regression suite.

No live provider or real user data. SQLite here tests logical correctness;
concurrent PostgreSQL worker admission is tested in the dedicated CI job.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from im19_mcp.distributed_quota import (
    DistributedQuota, QuotaUnavailable, RateWindow,
)
from im19_mcp.rate_limit import DistributedRateLimitMiddleware
from im19_mcp.storage import Vault
from cryptography.fernet import Fernet


@pytest.fixture
def quota(tmp_path):
    db_url = "sqlite:///" + str(tmp_path / "quotas.sqlite3")
    vault = Vault(
        db_url, encryption_key=Fernet.generate_key().decode(),
        audit_salt="test-audit-salt",
    )
    vault.initialize_schema()
    return DistributedQuota(vault.engine, secret="synthetic-strong-test-salt")


def test_exact_quota_and_window_rotation(quota):
    start = 1_400_000_000
    assert quota.consume("tool:reads", "subject-1", limit=2, now=start) == (True, 60 - start % 60)
    assert quota.consume("tool:reads", "subject-1", limit=2, now=start)[0] is True
    allowed, retry = quota.consume("tool:reads", "subject-1", limit=2, now=start)
    assert not allowed and 1 <= retry <= 60
    assert quota.consume("tool:reads", "subject-1", limit=2, now=start + 60)[0] is True


def test_subject_namespace_and_http_peer_isolation(quota):
    now = 1_500_000_000
    for namespace, identity in (
        ("mcp:provider-reads", "user-a"),
        ("mcp:provider-reads", "user-b"),
        ("mcp:oauth-begin", "user-a"),
        ("http:peer", "user-a"),
    ):
        assert quota.consume(namespace, identity, limit=1, now=now)[0]
        assert not quota.consume(namespace, identity, limit=1, now=now)[0]


def test_no_raw_ip_or_user_in_database(quota):
    ip = "192.0.2.100"
    subject = "identity-with-secret-marker"
    quota.consume("http:peer", ip, limit=2)
    quota.consume("mcp:provider-reads", subject, limit=2)
    with quota.sessions() as session:
        rows = session.execute(select(RateWindow)).scalars().all()
    assert len(rows) == 2
    assert all(len(x.key_digest) == 64 and x.hits == 1 for x in rows)
    assert all(ip not in x.key_digest and subject not in x.key_digest for x in rows)


@pytest.mark.parametrize("limit,window", [
    (0, 60), (-1, 60), (True, 60), (100001, 60),
    (1, 0), (1, -1), (1, 86401), (1, True),
])
def test_invalid_quota_limits_fail_closed(quota, limit, window):
    with pytest.raises(ValueError, match="invalid_quota_parameters"):
        quota.consume("mcp:reads", "user-1", limit=limit, window_seconds=window)


@pytest.mark.parametrize("namespace,identity", [
    ("", "user-1"), ("mcp:reads", ""), ("mcp:reads", "x" * 513),
    ("x" * 97, "user-1"),
])
def test_invalid_quota_identity_rejected(quota, namespace, identity):
    with pytest.raises(ValueError, match="quota_identity_invalid"):
        quota.consume(namespace, identity, limit=10)


def test_multiworker_contention_consistent_counter(quota):
    def call(_):
        return quota.consume("http:peer", "198.51.100.30", limit=6, now=1_500_000_000)[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, range(16)))
    assert sum(results) == 6
    with quota.sessions() as session:
        hits = session.execute(select(RateWindow.hits)).scalar_one()
    assert hits == 6


def test_expiry_cleanup(quota):
    now = 1_500_000_000
    quota.consume("http:peer", "198.51.100.20", limit=10, now=now)
    assert quota.purge_expired(now=now) == 0
    assert quota.purge_expired(now=now + 190) == 1
    assert quota.purge_expired(now=now + 190) == 0


def test_storage_outage_does_not_allow_unmetered_request(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "missing-tables.sqlite3"))
    quota = DistributedQuota(engine, secret="synthetic-strong-test-salt")
    with pytest.raises(QuotaUnavailable):
        quota.consume("http:peer", "192.0.2.10", limit=10)


@pytest.mark.asyncio
async def test_asgi_middleware_distributed_limit_and_bypass_protection(quota):
    app = Starlette(routes=[
        Route("/mcp", lambda _: JSONResponse({"ok": True})),
        Route("/health", lambda _: JSONResponse({"status": "alive"})),
    ])
    wrapper = DistributedRateLimitMiddleware(app, quota=quota, requests_per_minute=2)
    transport = httpx.ASGITransport(app=wrapper, client=("198.51.100.7", 50001))
    async with httpx.AsyncClient(transport=transport, base_url="https://mcp.test") as client:
        assert (await client.get("/mcp")).status_code == 200
        assert (await client.get("/mcp", headers={"X-Forwarded-For": "203.0.113.3"})).status_code == 200
        exhausted = await client.get("/mcp", headers={"X-Forwarded-For": "203.0.113.4"})
        assert exhausted.status_code == 429
        assert exhausted.json() == {"error": "RATE_LIMITED"}
        assert exhausted.headers["Cache-Control"] == "no-store"
        assert 1 <= int(exhausted.headers["Retry-After"]) <= 60
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_asgi_quota_storage_outage_returns_503(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "no-migration.sqlite3"))
    quota = DistributedQuota(engine, secret="synthetic-strong-test-salt")
    app = Starlette(routes=[Route("/mcp", lambda _: JSONResponse({"ok": True}))])
    wrapper = DistributedRateLimitMiddleware(app, quota=quota)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper, client=("192.0.2.1", 3333)),
        base_url="https://mcp.test",
    ) as client:
        result = await client.get("/mcp")
        assert result.status_code == 503
        assert result.json() == {"error": "QUOTA_UNAVAILABLE"}


@pytest.mark.asyncio
async def test_asgi_missing_network_peer_fails_closed(quota):
    app = Starlette(routes=[Route("/mcp", lambda _: JSONResponse({"ok": True}))])
    wrapper = DistributedRateLimitMiddleware(app, quota=quota)
    scope = {"type": "http", "path": "/mcp", "client": None}
    frames = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(frame):
        frames.append(frame)

    await wrapper(scope, receive, send)
    assert frames[0]["status"] == 503
