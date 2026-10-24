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
