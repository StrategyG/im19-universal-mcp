"""PostgreSQL 16 integration tests, never run with production credentials.

Run only in dedicated disposable CI DB:
  IM19_INTEGRATION_DB_URL=postgresql+psycopg://... pytest -q tests/integration
"""
from __future__ import annotations

import os
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from im19_mcp.governance.approvals import (
    ApprovalDenied, ApprovalLedger, DuplicateExecution, MutationReceipt, payload_sha256,
)
from im19_mcp.storage import Vault

pytestmark = pytest.mark.skipif(
    not os.getenv("IM19_INTEGRATION_DB_URL"),
    reason="Disposable PostgreSQL integration database not configured",
)


def test_postgres_one_approval_allows_only_one_concurrent_reservation():
    url = os.environ["IM19_INTEGRATION_DB_URL"]
    assert url.startswith("postgresql+psycopg://"), "CI must use actual PostgreSQL"
    # The CI job runs migrations before the test, verifying the production schema.
    vault = Vault(url, encryption_key=os.environ["IM19_INTEGRATION_FERNET_KEY"],
                  audit_salt="only-postgres-ci-audit-salt")
    ledger = ApprovalLedger(vault)
    identity = secrets.token_hex(12)
    payload = {"document": identity, "body": "sample"}
    code = ledger.issue(
        subject=identity, provider="gmail", account_id="account-test",
        action="gmail.send", resource_id="draft-test",
        payload_hash=payload_sha256(payload), approver="separate-approver",
        evidence_reference="ci-portal-evidence-123", four_eyes=True,
    )

    def attempt(i: int) -> str:
        try:
            ledger.reserve(
                token=code, subject=identity, provider="gmail",
                account_id="account-test", action="gmail.send",
                resource_id="draft-test", payload=payload,
                idempotency_key=f"pg-race-{identity}-{i}",
            )
            return "RESERVED"
        except (ApprovalDenied, DuplicateExecution):
            return "DENIED"

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(8)))
    assert statuses.count("RESERVED") == 1
    assert statuses.count("DENIED") == 7
    with vault.sessions() as session:
        receipts = session.execute(select(MutationReceipt).where(
            MutationReceipt.subject == identity,
        )).scalars().all()
        assert len(receipts) == 1


def test_postgres_unique_idempotency_rejects_two_distinct_approvals():
    url = os.environ["IM19_INTEGRATION_DB_URL"]
    vault = Vault(url, encryption_key=os.environ["IM19_INTEGRATION_FERNET_KEY"],
                  audit_salt="only-postgres-ci-audit-salt")
    ledger = ApprovalLedger(vault)
    identity = secrets.token_hex(12)
    payload = {"resource": "row", "count": 1}
    data = dict(
        subject=identity, provider="drive", account_id="account-test",
        action="drive.write", resource_id="row-test",
        payload_hash=payload_sha256(payload), approver="human-approver",
        evidence_reference="ci-human-consent-123",
    )
    codes = [ledger.issue(**data) for _ in range(2)]
    idem = f"pg-idempotency-{identity}"

    def attempt(code: str) -> str:
        try:
            ledger.reserve(
                token=code, subject=identity, provider="drive",
                account_id="account-test", action="drive.write",
                resource_id="row-test", payload=payload, idempotency_key=idem,
            )
            return "RESERVED"
        except (ApprovalDenied, DuplicateExecution):
            return "DENIED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, codes))
    assert results.count("RESERVED") == 1
    assert results.count("DENIED") == 1
    with vault.sessions() as session:
        assert len(session.execute(select(MutationReceipt).where(
            MutationReceipt.subject == identity,
        )).scalars().all()) == 1


def test_postgres_atomic_offline_key_rotation_covers_all_sensitive_tables():
    """Real PostgreSQL transaction: old-key ciphertext is rewrapped in one pass."""
    import time
    from cryptography.fernet import Fernet, InvalidToken

    url = os.environ["IM19_INTEGRATION_DB_URL"]
    old_key = os.environ["IM19_INTEGRATION_FERNET_KEY"]
    new_key = Fernet.generate_key().decode()
    suffix = secrets.token_hex(12)
    old = Vault(url, encryption_key=old_key, audit_salt="ci-test-salt")
    old.put_credential(
        suffix, "drive", "google-account-mock",
        {"access_token": "simulated-secret"}, time.time() + 900,
    )
    state = f"ci-oauth-{suffix}"
    old.create_state(state, suffix, ("openid",), "mock-verifier", time.time() + 100)
    claim = old.stage_link(
        suffix, "youtube", "google-account-mock",
        {"access_token": "simulated-pending-token"}, time.time() + 900,
    )

    rotating = Vault(url, encryption_key=new_key, previous_keys=(old_key,))
    assert rotating.get_credential(suffix, "drive") is not None
    # Rotation is an OFFLINE operation. This disposable CI DB has no writers
    # while rotating, and contains no real credentials or customer data.
    counts = rotating.rotate_encryption(offline_maintenance_ack=True)
    assert counts["oauth_credentials"] >= 1
    assert counts["oauth_states"] >= 1
    assert counts["oauth_pending_links"] >= 1

    primary = Vault(url, encryption_key=new_key)
    assert primary.verify_primary_key_coverage() == counts
    assert primary.get_credential(suffix, "drive")[1]["access_token"] == "simulated-secret"
    assert primary.consume_state(state)[-1] == "mock-verifier"
    assert primary.confirm_link(suffix, "youtube", claim)[1]["access_token"] == (
        "simulated-pending-token"
    )
    with pytest.raises(InvalidToken):
        old.get_credential(suffix, "drive")


def test_postgres_shared_quota_across_parallel_gateway_workers():
    """Atomic shared limit holds on PostgreSQL 16 under concurrent requests."""
    from sqlalchemy import create_engine
    from im19_mcp.distributed_quota import DistributedQuota, RateWindow

    url = os.environ["IM19_INTEGRATION_DB_URL"]
    # Simulate distinct gateway instances with separate connection pools.
    replicas = [
        DistributedQuota(create_engine(url, pool_pre_ping=True), secret="ci-rate-salt-123456")
        for _ in range(4)
    ]
    identity = secrets.token_hex(16)
    now = 1_610_000_001
    limit = 10

    def request(i: int) -> bool:
        return replicas[i % len(replicas)].consume(
            "http:peer", identity, limit=limit, now=now
        )[0]

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(request, range(40)))
    assert sum(outcomes) == limit
    digest = replicas[0]._digest("http:peer", identity, 60)
    with replicas[0].sessions() as session:
        records = session.execute(select(RateWindow).where(
            RateWindow.key_digest == digest,
        )).scalars().all()
        assert len(records) == 1
        assert records[0].hits == limit
        assert identity not in records[0].key_digest
    for replica in replicas:
        replica.engine.dispose()


def test_postgres_shared_quota_subject_isolation_and_retention():
    from sqlalchemy import create_engine
    from im19_mcp.distributed_quota import DistributedQuota

    url = os.environ["IM19_INTEGRATION_DB_URL"]
    limiter = DistributedQuota(create_engine(url), secret="ci-rate-salt-123456")
    try:
        subject = secrets.token_hex(16)
        now = 1_700_000_000
        assert limiter.consume("mcp:provider-reads", subject, limit=1, now=now)[0]
        assert not limiter.consume("mcp:provider-reads", subject, limit=1, now=now)[0]
        assert limiter.consume("mcp:oauth-begin", subject, limit=1, now=now)[0]
        assert limiter.consume("mcp:provider-reads", subject + "-2", limit=1, now=now)[0]
        # A shared ledger maintenance job can remove expired windows safely.
        assert limiter.purge_expired(now=now + 100_000) >= 3
    finally:
        limiter.engine.dispose()


def test_postgres_quota_accepts_64bit_expiration_after_2038():
    from sqlalchemy import create_engine
    from im19_mcp.distributed_quota import DistributedQuota, RateWindow

    url = os.environ["IM19_INTEGRATION_DB_URL"]
    quota = DistributedQuota(create_engine(url), secret="ci-rate-salt-123456")
    try:
        subject = secrets.token_hex(16)
        year_2046 = 2_400_000_000
        assert quota.consume("http:peer", subject, limit=1, now=year_2046)[0]
        digest = quota._digest("http:peer", subject, 60)
        with quota.sessions() as session:
            row = session.execute(
                select(RateWindow).where(RateWindow.key_digest == digest)
            ).scalar_one()
            assert row.expires_at > 2**31 - 1
    finally:
        quota.engine.dispose()


def test_postgres_competing_first_links_never_rebind_an_account():
    import time
    from im19_mcp.storage import AuditEvent

    url = os.environ["IM19_INTEGRATION_DB_URL"]
    workers = [Vault(url, encryption_key=os.environ["IM19_INTEGRATION_FERNET_KEY"],
                     audit_salt="synthetic-confirmation-salt") for _ in range(2)]
    subject = secrets.token_hex(16)
    codes = [workers[i].stage_link(subject, "drive", f"account-{i}",
             {"access_token": "synthetic", "browser_verified": True}, time.time() + 300) for i in range(2)]

    def confirm(i):
        try:
            workers[i].activate_link(subject, "drive", codes[i], require_browser=True)
            return "CONNECTED"
        except PermissionError:
            return "DENIED"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(confirm, range(2)))
        assert outcomes.count("CONNECTED") == 1
        assert outcomes.count("DENIED") == 1
        winner = outcomes.index("CONNECTED")
        assert workers[0].get_credential(subject, "drive")[0] == f"account-{winner}"
        pseudonym = workers[0]._audit_record(subject, "drive", "oauth.confirm", "SUCCESS").subject_pseudonym
        with workers[0].sessions() as session:
            records = session.execute(select(AuditEvent).where(
                AuditEvent.subject_pseudonym == pseudonym, AuditEvent.action == "oauth.confirm",
            )).scalars().all()
            assert len(records) == 1
    finally:
        for worker in workers:
            worker.engine.dispose()

