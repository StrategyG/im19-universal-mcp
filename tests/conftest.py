"""Synthetic configuration only; tests never require provider credentials."""
import pytest
from cryptography.fernet import Fernet

from im19_mcp.config import Settings


@pytest.fixture
def remote_cfg(tmp_path):
    return Settings(
        database_url="sqlite:///" + str(tmp_path / "remote.sqlite3"),
        vault_key=Fernet.generate_key().decode(), audit_salt="synthetic-audit-salt-" + "x" * 32,
        google_client_id="google-test", google_client_secret="google-secret-test",
        google_redirect_uri="https://mcp.test/oauth/google/callback",
        mcp_issuer="https://identity.test", mcp_resource="https://mcp.test/mcp",
        mcp_introspection="https://identity.test/introspect",
        mcp_client_id="resource-test", mcp_client_secret="resource-secret-test", dev_subject="",
        deployment_environment="staging",
        browser_authorization="https://identity.test/authorize",
        browser_token="https://identity.test/token",
        browser_client_id="browser-test", browser_client_secret="browser-secret-test",
    )
