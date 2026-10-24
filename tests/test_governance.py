"""Institutional governance acceptance: grants, single-use, scope, idempotency.

These are database/mock tests. No human-authenticated portal or external API
mutation is asserted to exist in the project.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from im19_mcp.governance.approvals import (
    ApprovalDenied, ApprovalGrant, ApprovalLedger, DuplicateExecution,
    MutationReceipt, payload_sha256,
)
from im19_mcp.storage import AuditEvent, Vault


@pytest.fixture
def ledger(tmp_path):
    vault = Vault(
        "sqlite:///" + str(tmp_path / "gov.sqlite3"),
        encryption_key=Fernet.generate_key().decode(), audit_salt="trusted-test-salt",
    )
    vault.initialize_schema()
    return ApprovalLedger(vault)


PAYLOAD = {"message": "Hello", "targets": [1, 2], "metadata": {"a": 1, "z": 2}}


def grant(ledger, **overrides):
    args = dict(
        subject="user-1", provider="gmail", account_id="google-a",
        action="gmail.send", resource_id="draft-123",
        payload_hash=payload_sha256(PAYLOAD),
        approver="human-approver", evidence_reference="portal-event-123456",
    )
    args.update(overrides)
    return ledger.issue(**args)


def reserve(ledger, token, **overrides):
    args = dict(
        token=token, subject="user-1", provider="gmail",
        account_id="google-a", action="gmail.send",
        resource_id="draft-123", payload=PAYLOAD,
        idempotency_key="request-abc-123",
    )
    args.update(overrides)
    return ledger.reserve(**args)


def test_canonical_payload_hash_key_order_invariant():
    clone = {"targets": [1, 2], "metadata": {"z": 2, "a": 1}, "message": "Hello"}
    assert payload_sha256(PAYLOAD) == payload_sha256(clone)


@pytest.mark.parametrize("payload", [
    float("nan"), ["one"], {"bad": float("nan")}, {"huge": "x" * 70000},
    {1: "nonstring"},
])
def test_noncanonical_or_excessive_payload_fails(payload):
    with pytest.raises(ValueError):
        payload_sha256(payload)


def test_issue_does_not_store_plaintext_code_and_has_audit(ledger):
    code = grant(ledger)
    with ledger.vault.sessions() as session:
        row = session.execute(select(ApprovalGrant)).scalar_one()
        assert len(row.code_hash) == 64
        assert code not in row.code_hash
        assert row.subject == "user-1"
        event = session.execute(select(AuditEvent)).scalar_one()
        assert event.subject_pseudonym != "user-1"
        assert event.action == "governance.issue"
    assert len(code) >= 32


def test_same_user_exact_payload_single_use(ledger):
    code = grant(ledger)
    receipt = reserve(ledger, code)
    assert receipt.status == "RESERVED"
    assert receipt.payload_hash == payload_sha256(PAYLOAD)
    assert ledger.get_receipt(subject="user-1", reservation_id=receipt.reservation_id) == receipt
    assert ledger.get_receipt(subject="user-2", reservation_id=receipt.reservation_id) is None
    with pytest.raises((ApprovalDenied, DuplicateExecution)):
        reserve(ledger, code, idempotency_key="different-key-1")


@pytest.mark.parametrize("override", [
    {"subject": "user-2"},
    {"provider": "drive"},
    {"account_id": "google-b"},
    {"action": "gmail.delete"},
    {"resource_id": "draft-999"},
    {"payload": {"message": "ALTERED"}},
])
def test_scope_denied_without_spending_valid_grant(ledger, override):
    code = grant(ledger)
    with pytest.raises(ApprovalDenied):
        reserve(ledger, code, idempotency_key="request-1-mismatch", **override)
    # Rejected requests may not spend another user's or another payload's approval.
    assert reserve(ledger, code).status == "RESERVED"


def test_four_eyes_self_approval_denied(ledger):
    with pytest.raises(ApprovalDenied, match="four_eyes"):
        grant(ledger, approver="user-1", four_eyes=True)
    code = grant(ledger, approver="security-approver", four_eyes=True)
    assert reserve(ledger, code).status == "RESERVED"


def test_new_grant_cannot_reuse_same_idempotency_key(ledger):
    first = grant(ledger)
    reservation = reserve(ledger, first)
    assert reservation.status == "RESERVED"
    second = grant(ledger)
    with pytest.raises(DuplicateExecution):
        reserve(ledger, second)
    # The key clash must not silently consume a separate, valid grant.
    assert reserve(ledger, second, idempotency_key="request-second-456").status == "RESERVED"


def test_finalize_is_one_time_and_subject_bound(ledger):
    receipt = reserve(ledger, grant(ledger))
    with pytest.raises(ValueError, match="mutation_outcome_invalid"):
        ledger.finalize(reservation_id=receipt.reservation_id, subject="user-1", outcome="RETRY")
    assert ledger.finalize(
        reservation_id=receipt.reservation_id, subject="user-2", outcome="SUCCEEDED"
    ) is False
    assert ledger.finalize(
        reservation_id=receipt.reservation_id, subject="user-1", outcome="SUCCEEDED"
    ) is True
    assert ledger.finalize(
        reservation_id=receipt.reservation_id, subject="user-1", outcome="SUCCEEDED"
    ) is False
    row = ledger.get_receipt(subject="user-1", reservation_id=receipt.reservation_id)
    assert row is not None and row.status == "SUCCEEDED"
    with ledger.vault.sessions() as session:
        assert session.execute(select(MutationReceipt)).scalar_one().status == "SUCCEEDED"
        actions = [row.action for row in session.execute(select(AuditEvent)).scalars().all()]
        assert "governance.reserve" in actions and "governance.finalize" in actions


def test_expired_approval_is_denied(ledger):
    code = grant(ledger)
    with ledger.vault.sessions.begin() as session:
        session.query(ApprovalGrant).update({ApprovalGrant.expires_at: time.time() - 1})
    with pytest.raises(ApprovalDenied):
        reserve(ledger, code)
    assert ledger.purge_expired_approvals() == 1


def test_wrong_key_and_unknown_reservation(ledger):
    code = grant(ledger)
    with pytest.raises(ValueError, match="idempotency_key_invalid"):
        reserve(ledger, code, idempotency_key="bad")
    assert ledger.get_receipt(subject="user-1", reservation_id="unknown") is None
    with pytest.raises(ApprovalDenied):
        reserve(ledger, "random-invalid-token-long-enough")


def test_concurrent_reservations_only_one_wins(ledger):
    code = grant(ledger)
    def attempt(index):
        try:
            return reserve(ledger, code, idempotency_key=f"concurrent-key-{index}")
        except (ApprovalDenied, DuplicateExecution):
            return None

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(attempt, range(6)))
    assert len([result for result in results if result is not None]) == 1
    with ledger.vault.sessions() as session:
        assert len(session.execute(select(MutationReceipt)).scalars().all()) == 1


def test_audit_does_not_contain_sensitive_payload(ledger):
    secret = "sensitive-do-not-log"
    payload = {"body": secret}
    code = grant(ledger, payload_hash=payload_sha256(payload))
    reserve(ledger, code, payload=payload)
    with ledger.vault.sessions() as session:
        events = session.execute(select(AuditEvent)).scalars().all()
    assert secret not in json.dumps([
        {k: getattr(event, k) for k in ("subject_pseudonym", "service", "action", "status", "request_id")}
        for event in events
    ])
