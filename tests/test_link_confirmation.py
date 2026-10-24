"""Security acceptance tests: token claims are encrypted, short-lived and one-time."""
import time

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from im19_mcp.storage import Vault


@pytest.fixture
def vault(tmp_path):
    store = Vault(
        "sqlite:///" + str(tmp_path / "claim.sqlite3"),
        encryption_key=Fernet.generate_key().decode(),
        audit_salt="test-audit-salt",
    )
    store.initialize_schema()
    return store


def test_google_callback_does_not_persist_usable_token(vault):
    code = vault.stage_link(
        "mcp-user-a", "youtube", "google-account-a",
        {"access_token": "access-secret", "refresh_token": "refresh-secret",
         "scopes": ["openid"]},
        time.time() + 3600,
    )
    assert vault.get_credential("mcp-user-a", "youtube") is None
    with vault.engine.connect() as conn:
        row = conn.execute(text(
            "SELECT confirmation_hash, token_ciphertext FROM oauth_pending_links"
        )).one()
    assert code not in row[0] and "access-secret" not in row[1]
    assert "refresh-secret" not in row[1]


def test_confirmation_wrong_subject_or_service_fails_without_consuming(vault):
    code = vault.stage_link(
        "user-a", "drive", "account-12345",
        {"access_token": "token", "scopes": ["drive"]}, time.time() + 300,
    )
    with pytest.raises(PermissionError):
        vault.confirm_link("user-b", "drive", code)
    with pytest.raises(PermissionError):
        vault.confirm_link("user-a", "youtube", code)
    account, secret, expiry = vault.confirm_link("user-a", "drive", code)
    assert account == "account-12345"
    assert secret["access_token"] == "token"
    assert expiry > time.time()


def test_claim_reuse_is_denied_across_server_restart(vault, tmp_path):
    db_url = vault.engine.url.render_as_string(hide_password=False)
    key = vault.cipher._signing_key + vault.cipher._encryption_key
    import base64
    fernet_key = base64.urlsafe_b64encode(key).decode("ascii")
    code = vault.stage_link(
        "user-a", "youtube", "google-account",
        {"access_token": "test"}, time.time() + 300,
    )
    restarted = Vault(db_url, encryption_key=fernet_key, audit_salt="test-audit-salt")
    restarted.confirm_link("user-a", "youtube", code)
    with pytest.raises(PermissionError):
        vault.confirm_link("user-a", "youtube", code)


def test_expired_confirmation_fails_and_is_consumed(vault):
    code = vault.stage_link(
        "user-a", "drive", "google-account",
        {"access_token": "secret"}, time.time() + 600,
        ttl_seconds=30,
    )
    from sqlalchemy import update
    from im19_mcp.storage import PendingLink
    with vault.sessions.begin() as session:
        session.execute(update(PendingLink).values(expires_at=time.time() - 1))
    for _ in range(2):
        with pytest.raises(PermissionError):
            vault.confirm_link("user-a", "drive", code)


def test_wrong_confirmation_code_is_denied(vault):
    vault.stage_link(
        "user-a", "youtube", "google-account",
        {"access_token": "secret"}, time.time() + 300,
    )
    with pytest.raises(PermissionError):
        vault.confirm_link("user-a", "youtube", "wrong-confirmation-code-123456")
