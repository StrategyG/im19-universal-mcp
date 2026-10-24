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
        deployment_environment="staging",
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


def test_production_config_denies_sqlite_dev_subject_and_weak_salt(cfg):
    prod = replace(cfg, deployment_environment="production")
    with pytest.raises(ValueError, match="production_requires_postgresql"):
        prod.require_remote()
    strong = replace(
        prod, database_url="postgresql+psycopg://user:pass@host/db"
    )
    with pytest.raises(ValueError, match="production_dev_subject_forbidden"):
        strong.require_remote()
    with pytest.raises(ValueError, match="production_requires_strong_audit_salt"):
        replace(strong, dev_subject="").require_remote()
    replace(strong, dev_subject="", audit_salt="x" * 32).require_remote()
    with pytest.raises(ValueError, match="production_legacy_key_fallback_forbidden"):
        replace(
            strong, dev_subject="", audit_salt="x" * 32,
            vault_previous_keys=("synthetic-fallback-value",),
        ).require_remote()


def test_create_app_requires_valid_issuer_settings(cfg):
    # create_app performs security validation prior to binding ports.
    with pytest.raises(ValueError):
        create_app(replace(cfg, mcp_client_secret=""))


@pytest.mark.asyncio
async def test_google_callback_only_stages_secret_and_protects_browser_response(cfg):
    from cryptography.fernet import Fernet
    from unittest.mock import AsyncMock

    from im19_mcp.google_oauth import GoogleOAuth

    configured = replace(
        cfg, vault_key=Fernet.generate_key().decode(),
        google_client_id="unit-test-google-client", google_client_secret="mock-secret",
        google_redirect_uri="https://mcp.test/oauth/google/callback",
    )
    with patch.object(GoogleOAuth, "complete", new_callable=AsyncMock) as complete:
        complete.return_value = {
            "status": "PENDING_CONFIRMATION",
            "confirmation_code": "unit-test-only-not-real",
        }
        app = create_app(configured)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://mcp.test",
        ) as client:
            response = await client.get(
                "/oauth/google/callback", params={"state": "some-state", "code": "mock-code"},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "PENDING_CONFIRMATION"
            assert response.headers["Cache-Control"] == "no-store, private"
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert response.headers["X-Frame-Options"] == "DENY"
            assert "default-src 'none'" in response.headers["Content-Security-Policy"]
            complete.assert_awaited_once_with(state="some-state", code="mock-code")

            dup = await client.get(
                "/oauth/google/callback?state=first&state=second&code=mock-code",
            )
            assert dup.status_code == 400
            assert dup.json()["status"] == "OAUTH_FAILED"
            assert complete.await_count == 1


@pytest.mark.asyncio
async def test_readiness_503_without_db_schema_and_200_after_revision(cfg, tmp_path):
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine, text

    db = "sqlite:///" + str(tmp_path / "gateway-ready.sqlite3")
    configured = replace(cfg, database_url=db, vault_key=Fernet.generate_key().decode())
    app = create_app(configured)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://mcp.test",
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        initial = await client.get("/ready")
        assert initial.status_code == 503
        assert initial.json() == {"status": "unavailable"}
        assert initial.headers["Cache-Control"] == "no-store"

        engine = create_engine(db)
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO alembic_version (version_num) VALUES ('0005_distributed_quotas')"
            ))
        engine.dispose()

        ready = await client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_production_gateway_uses_shared_quota_and_fails_closed_without_database(cfg):
    from cryptography.fernet import Fernet
    from im19_mcp.rate_limit import DistributedRateLimitMiddleware, RateLimitMiddleware

    # Intentionally unreachable PostgreSQL endpoint. Neither a real account
    # nor a live third-party provider is involved in this configuration test.
    production = replace(
        cfg, deployment_environment="production",
        database_url="postgresql+psycopg://im19:test@127.0.0.1:65531/none",
        dev_subject="", audit_salt="strong-test-audit-salt-" + ("z" * 40),
        vault_key=Fernet.generate_key().decode(),
    )
    app = create_app(production)
    assert any(
        middleware.cls is DistributedRateLimitMiddleware
        for middleware in app.user_middleware
    )
    assert not any(
        middleware.cls is RateLimitMiddleware
        for middleware in app.user_middleware
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.3", 1234)),
        base_url="https://mcp.test",
    ) as client:
        # Availability of the HTTP process is not permission to use MCP.
        assert (await client.get("/health")).status_code == 200
        failed = await client.get("/mcp")
        assert failed.status_code == 503
        assert failed.json() == {"error": "QUOTA_UNAVAILABLE"}
