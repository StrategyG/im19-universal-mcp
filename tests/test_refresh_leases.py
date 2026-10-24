"""Cross-worker OAuth refresh safety regression tests."""
import time

import pytest
from cryptography.fernet import Fernet

from im19_mcp.storage import Vault


@pytest.fixture
def vault(tmp_path):
    result = Vault(
        "sqlite:///" + str(tmp_path / "refresh.sqlite3"),
        encryption_key=Fernet.generate_key().decode(), audit_salt="test-salt",
    )
    result.initialize_schema()
    return result


def old_token():
    return {"access_token": "stale-access", "refresh_token": "refresh-secret", "scopes": ["openid"]}


def test_refresh_lease_only_one_owner_and_other_worker_cannot_release(vault):
    vault.put_credential("user", "youtube", "google_account", old_token(), time.time() - 1)
    first = vault.acquire_refresh_lease("user", "youtube", "worker-a", now=time.time())
    assert first is not None and first[0] == "google_account"
    assert vault.acquire_refresh_lease("user", "youtube", "worker-b", now=time.time()) is None
    vault.release_refresh_lease("user", "youtube", "worker-b")
    assert vault.acquire_refresh_lease("user", "youtube", "worker-c", now=time.time()) is None
    vault.release_refresh_lease("user", "youtube", "worker-a")
    assert vault.acquire_refresh_lease("user", "youtube", "worker-d", now=time.time()) is not None


def test_expired_lease_can_be_stolen_and_old_worker_cannot_write(vault):
    vault.put_credential("user", "drive", "account", old_token(), time.time() - 1)
    start = time.time()
    assert vault.acquire_refresh_lease("user", "drive", "old", now=start, lease_seconds=10)
    assert vault.acquire_refresh_lease("user", "drive", "new", now=start + 11, lease_seconds=10)
    assert vault.complete_refresh("user", "drive", "old", "account",
                                   {"access_token": "old-value"}, time.time() + 100) is False
    assert vault.complete_refresh("user", "drive", "new", "account",
                                   {"access_token": "new-value"}, time.time() + 100) is True
    assert vault.get_credential("user", "drive")[1]["access_token"] == "new-value"


def test_disconnect_during_refresh_cannot_resurrect_token(vault):
    vault.put_credential("user", "youtube", "account", old_token(), time.time() - 1)
    assert vault.acquire_refresh_lease("user", "youtube", "worker", now=time.time())
    assert vault.delete_credential("user", "youtube") is True
    assert not vault.complete_refresh("user", "youtube", "worker", "account",
                                      {"access_token": "resurrected"}, time.time() + 100)
    assert vault.get_credential("user", "youtube") is None


def test_reauthorization_invalidates_old_refresh_worker(vault):
    vault.put_credential("user", "youtube", "account", old_token(), time.time() - 1)
    assert vault.acquire_refresh_lease("user", "youtube", "worker", now=time.time())
    vault.put_credential(
        "user", "youtube", "account", {"access_token": "new-consent"}, time.time() + 100,
    )
    assert vault.complete_refresh("user", "youtube", "worker", "account",
                                   {"access_token": "stale"}, time.time() + 100) is False
    assert vault.get_credential("user", "youtube")[1]["access_token"] == "new-consent"
