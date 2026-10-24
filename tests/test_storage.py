import sqlite3
import time

import pytest
from cryptography.fernet import Fernet

from im19_mcp.storage import Vault


@pytest.fixture
def vault(tmp_path):
    v = Vault("sqlite:///" + str(tmp_path / "test.sqlite3"),
              encryption_key=Fernet.generate_key().decode(), audit_salt="test-salt")
    v.initialize_schema()
    return v


def test_oauth_state_one_time(vault):
    vault.create_state("secret-state", "user1", ("openid",), "pkce-secret", time.time() + 30)
    assert vault.consume_state("secret-state") == ("user1", ("openid",), "pkce-secret")
    with pytest.raises(PermissionError):
        vault.consume_state("secret-state")


def test_expired_state_denied(vault):
    vault.create_state("expired", "user1", (), "secret", time.time() - 2)
    with pytest.raises(PermissionError):
        vault.consume_state("expired")


def test_encrypted_credentials_isolated_per_user(vault):
    vault.put_credential("user1", "youtube", "account1",
                         {"access_token": "supersecret", "scopes": ["openid"]}, time.time() + 99)
    assert vault.get_credential("user2", "youtube") is None
    assert vault.get_credential("user1", "youtube")[1]["access_token"] == "supersecret"
    with vault.engine.connect() as conn:
        from sqlalchemy import text
        raw = conn.execute(text("select token_ciphertext from oauth_credentials")).scalar_one()
        assert "supersecret" not in raw


def test_cross_account_rebind_denied(vault):
    vault.put_credential("user1", "youtube", "account1", {"access_token": "one"}, 999)
    with pytest.raises(PermissionError):
        vault.put_credential("user1", "youtube", "account2", {"access_token": "two"}, 999)


def test_disconnect(vault):
    assert vault.delete_credential("user1", "youtube") is False
    vault.put_credential("user1", "youtube", "account1", {"access_token": "one"}, 999)
    assert vault.delete_credential("user1", "youtube") is True
    assert vault.get_credential("user1", "youtube") is None


def test_audit_does_not_store_identity_or_secrets(vault):
    vault.audit("very_private_subject", "youtube", "read_channel", "SUCCESS")
    from sqlalchemy import text
    with vault.engine.connect() as conn:
        row = conn.execute(text("select subject_pseudonym, service, action from audit_events")).one()
    assert row[0] != "very_private_subject"
    assert len(row[0]) == 64
    assert row[1:] == ("youtube", "read_channel")
