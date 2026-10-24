"""Remote gateway authentication unit tests; all issuer replies are mocked."""
import time
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest

from im19_mcp.config import Settings
from im19_mcp.gateway import IntrospectionVerifier, create_app


@pytest.fixture
def cfg():
    return Settings(
        database_url="sqlite://",
        vault_key="missing-valid-key-for-these-config-only-tests", audit_salt="salt",
        google_client_id="", google_client_secret="", google_redirect_uri="",
        mcp_issuer="https://identity.test", mcp_resource="https://mcp.test/mcp",
        mcp_introspection="https://identity.test/introspect",
        mcp_client_id="client", mcp_client_secret="secret",
        dev_subject="spoofed-stdio-value",
    )


def test_remote_config_fail_closed(cfg):
    with pytest.raises(ValueError, match="remote_auth_and_encryption_configuration_required"):
        replace(cfg, mcp_issuer="").require_remote()
    with pytest.raises(ValueError, match="HTTPS"):
        replace(cfg, mcp_resource="http://mcp.test/mcp").require_remote()


@pytest.mark.asyncio
@pytest.mark.parametrize("override,allowed", [
    ({}, True),
    ({"iss": "https://attacker.test"}, False),
    ({"aud": "https://mcp.test/other"}, False),
    ({"exp": 1}, False),
    ({"scope": "youtube.readonly"}, False),
    ({"sub": ""}, False),
    ({"active": False}, False),
])
async def test_mocked_introspection_checks_issuer_audience_scope_and_expiry(cfg, override, allowed):
    payload = {
        "active": True, "iss": cfg.mcp_issuer, "aud": cfg.mcp_resource,
        "exp": time.time() + 300, "client_id": "chatgpt-client",
        "sub": "trusted-subject", "scope": "im19:read",
    }
    payload.update(override)
    real_client = httpx.AsyncClient
    client = real_client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=payload)))
    with patch("im19_mcp.gateway.httpx.AsyncClient", return_value=client):
        result = await IntrospectionVerifier(cfg).verify_token("not-a-real-token")
    assert (result is not None) is allowed
    if allowed:
        assert result.subject == "trusted-subject"


def test_create_app_requires_valid_issuer_settings(cfg):
    # create_app performs security validation prior to binding ports.
    with pytest.raises(ValueError):
        create_app(replace(cfg, mcp_client_secret=""))
