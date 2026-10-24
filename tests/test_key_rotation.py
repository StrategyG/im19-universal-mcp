"""Encryption key lifecycle: controlled offline MultiFernet migration.

All test keys and OAuth tokens are randomly generated or synthetic; no
production secrets or real Google account are ever involved.
"""
from __future__ import annotations

import json
import time

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update

from im19_mcp.cli import rotate_keys, verify_primary_key
from im19_mcp.storage import Credential, PendingLink, PendingState, Vault


@pytest.fixture
def key_env(tmp_path):
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    url = "sqlite:///" + str(tmp_path / "rotation.sqlite3")
    old = Vault(url, encryption_key=old_key, audit_salt="test-salt")
    old.initialize_schema()
    return old, url, old_key, new_key


def populate(vault):
    vault.put_credential(
        "user-a", "youtube", "account-a",
        {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"},
        time.time() + 3600,
    )
    vault.create_state(
        "pending-state", "user-a", ("openid",), "synthetic-pkce", time.time() + 300,
    )
    return vault.stage_link(
        "user-a", "drive", "account-a",
        {"access_token": "synthetic-drive-access"}, time.time() + 3600,
    )


def test_offline_rotation_covers_credential_pkce_and_pending_link(key_env):
    old, url, old_key, new_key = key_env
    confirmation = populate(old)
    current = Vault(
        url, encryption_key=new_key, previous_keys=(old_key,), audit_salt="test-salt",
    )
    assert current.get_credential("user-a", "youtube")[1]["access_token"] == "synthetic-access"
    with pytest.raises(ValueError, match="primary_key_coverage_failed"):
        current.verify_primary_key_coverage()
    with pytest.raises(PermissionError, match="offline_maintenance_ack_required"):
        current.rotate_encryption()

    result = current.rotate_encryption(offline_maintenance_ack=True)
    assert result == {
        "oauth_credentials": 1, "oauth_states": 1, "oauth_pending_links": 1,
    }
    assert current.verify_primary_key_coverage() == result

    primary_only = Vault(url, encryption_key=new_key, audit_salt="test-salt")
    assert primary_only.verify_primary_key_coverage() == result
    assert primary_only.get_credential("user-a", "youtube")[1]["refresh_token"] == "synthetic-refresh"
    assert primary_only.consume_state("pending-state") == (
        "user-a", "youtube", ("openid",), "synthetic-pkce",
    )
    assert primary_only.confirm_link("user-a", "drive", confirmation)[1] == {
        "access_token": "synthetic-drive-access"
    }
    # A retired key may no longer decrypt freshly re-encrypted records.
    with pytest.raises(InvalidToken):
        old.get_credential("user-a", "youtube")


def test_encrypt_always_uses_primary_even_with_previous_keys(key_env):
    old, url, old_key, new_key = key_env
    migrating = Vault(url, encryption_key=new_key, previous_keys=(old_key,))
    migrating.put_credential(
        "fresh-user", "drive", "new-account",
        {"access_token": "new-key-only"}, time.time() + 3600,
    )
    assert Vault(url, encryption_key=new_key).get_credential(
        "fresh-user", "drive"
    )[1]["access_token"] == "new-key-only"
    with pytest.raises(InvalidToken):
        old.get_credential("fresh-user", "drive")


def test_corrupt_ciphertext_aborts_transaction_without_partial_rotation(key_env):
    old, url, old_key, new_key = key_env
    populate(old)
    with old.sessions.begin() as session:
        session.execute(
            update(PendingLink).values(token_ciphertext="bad-or-corrupt-ciphertext")
        )
    with old.sessions() as session:
        original = (
            session.execute(select(Credential.token_ciphertext)).scalar_one(),
            session.execute(select(PendingState.verifier_ciphertext)).scalar_one(),
        )
    rotating = Vault(url, encryption_key=new_key, previous_keys=(old_key,))
    with pytest.raises(ValueError, match="rotation_decryption_failed_database_unchanged"):
        rotating.rotate_encryption(offline_maintenance_ack=True)
    with old.sessions() as session:
        after = (
            session.execute(select(Credential.token_ciphertext)).scalar_one(),
            session.execute(select(PendingState.verifier_ciphertext)).scalar_one(),
        )
    assert original == after
    assert old.get_credential("user-a", "youtube")[1]["access_token"] == "synthetic-access"


def test_duplicate_keys_or_missing_previous_key_fail_closed(key_env):
    _, url, old_key, new_key = key_env
    with pytest.raises(ValueError, match="duplicate_encryption_keys_forbidden"):
        Vault(url, encryption_key=new_key, previous_keys=(new_key,))
    with pytest.raises(ValueError, match="duplicate_encryption_keys_forbidden"):
        Vault(url, encryption_key=new_key, previous_keys=(old_key, old_key))
    with pytest.raises(ValueError, match="fernet_key_invalid"):
        Vault(url, encryption_key="not-a-valid-fernet-key")
    with pytest.raises(ValueError, match="previous_key_required_for_rotation"):
        Vault(url, encryption_key=new_key).rotate_encryption(offline_maintenance_ack=True)


def test_cli_requires_explicit_offline_maintenance_and_never_logs_keys(
    key_env, monkeypatch, capsys,
):
    old, url, old_key, new_key = key_env
    populate(old)
    monkeypatch.setenv("IM19_DATABASE_URL", url)
    monkeypatch.setenv("IM19_FERNET_KEY", new_key)
    monkeypatch.setenv("IM19_FERNET_PREVIOUS_KEYS", old_key)
    monkeypatch.setenv("IM19_DEPLOYMENT_ENV", "staging")
    monkeypatch.delenv("IM19_OFFLINE_MAINTENANCE", raising=False)
    with pytest.raises(SystemExit, match="offline_maintenance_confirmation_required"):
        rotate_keys()
    monkeypatch.setenv("IM19_OFFLINE_MAINTENANCE", "YES")
    rotate_keys()
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "ROTATED_AND_VERIFIED"
    for sensitive in (old_key, new_key, "synthetic-access", "synthetic-refresh"):
        assert sensitive not in output
    # Verification is done with primary only, even when a fallback env key exists.
    verify_primary_key()
    assert json.loads(capsys.readouterr().out)["status"] == "PRIMARY_ONLY_VERIFIED"


def test_rotation_empty_vault_is_safe(key_env):
    _, url, old_key, new_key = key_env
    rotating = Vault(url, encryption_key=new_key, previous_keys=(old_key,))
    assert rotating.rotate_encryption(offline_maintenance_ack=True) == {
        "oauth_credentials": 0, "oauth_states": 0, "oauth_pending_links": 0,
    }
