"""HTTP protocol, cookie protections and worker switching without live accounts."""
import time
from dataclasses import replace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.server.auth.provider import AccessToken

from im19_mcp.browser_auth import BrowserAuthorization, COOKIE_NAME
from im19_mcp.gateway import IntrospectionVerifier, create_app
from im19_mcp.google_oauth import GoogleOAuth
from im19_mcp.storage import Vault


@pytest.mark.asyncio
async def test_all_routes_reject_foreign_hosts_origins_and_duplicate_headers(remote_cfg):
    app = create_app(remote_cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://mcp.test") as client:
        for path in ("/health", "/oauth/google/callback", "/connect/google", "/mcp"):
            assert (await client.get(path, headers={"Host": "evil.test"})).status_code == 421
            assert (await client.get(path, headers={"Origin": "https://evil.test"})).status_code == 403
        assert (await client.get("/health", headers=[("Host", "mcp.test"), ("Host", "evil.test")])).status_code == 421
        assert (await client.get("/health", headers=[("Origin", "https://mcp.test"),
                                                    ("Origin", "https://mcp.test")])).status_code == 403
        assert (await client.get("/health?x=" + "a" * 8193)).status_code == 414
        response = await client.get("/health", headers={"Origin": "https://mcp.test"})
        assert response.status_code == 200
        assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_production_headers_and_google_callback_require_browser(remote_cfg):
    production = replace(remote_cfg, deployment_environment="production",
                         database_url="postgresql+psycopg://test:synthetic@127.0.0.1:65531/none")
    app = create_app(production)
    # The public callback is protected even when called on the gateway directly.
    # Production rate admission normally rejects the absent DB before this route.
    with patch("im19_mcp.distributed_quota.DistributedQuota.consume", return_value=(True, 60)):
        with patch.object(GoogleOAuth, "complete", new_callable=AsyncMock) as complete:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://mcp.test") as client:
                r = await client.get("/oauth/google/callback?state=synthetic&code=synthetic")
                assert r.status_code == 400
                assert r.headers["Strict-Transport-Security"] == "max-age=31536000"
                complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_redirect_cookie_flags_and_replay(remote_cfg):
    store = Vault(remote_cfg.database_url, encryption_key=remote_cfg.vault_key)
    store.initialize_schema()
    browser = BrowserAuthorization(remote_cfg, store, GoogleOAuth(remote_cfg, store), IntrospectionVerifier(remote_cfg))
    ticket = browser.ticket("alice", "drive")["authorization_url"]
    app = create_app(remote_cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://mcp.test") as client:
        response = await client.get(ticket)
        assert response.status_code == 303
        cookie = response.headers["set-cookie"]
        assert COOKIE_NAME in cookie
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "Domain=" not in cookie
        assert response.headers["Cache-Control"] == "no-store"
        assert (await client.get(ticket)).status_code == 400
        assert (await client.get("/connect/google?ticket=a&ticket=b")).status_code == 400
        login_state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        # No cookie: reject without contacting the authorization server.
        client.cookies.clear()
        r = await client.get("/oauth/browser/callback", params={"state": login_state, "code": "code"})
        assert r.status_code == 400
    store.engine.dispose()


@pytest.mark.asyncio
async def test_stateless_mcp_can_switch_workers_without_identity_or_session_leak(remote_cfg):
    store = Vault(remote_cfg.database_url, encryption_key=remote_cfg.vault_key)
    store.initialize_schema()
    for subject in ("alice", "bob"):
        store.put_credential(subject, "drive", "google-" + subject,
                             {"access_token": "private-" + subject, "scopes": []}, time.time() + 3600)

    async def verify(token):
        if token not in {"alice", "bob"}:
            return None
        return AccessToken(token=token, client_id="test-client", subject=token,
                           resource=remote_cfg.mcp_resource, scopes=["im19:read"],
                           expires_at=int(time.time()) + 300)

    with patch.object(IntrospectionVerifier, "verify_token", side_effect=verify):
        first, second = create_app(remote_cfg), create_app(remote_cfg)
        async with first.router.lifespan_context(first), second.router.lifespan_context(second):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="https://mcp.test") as a:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="https://mcp.test") as b:
                    headers = {"Authorization": "Bearer alice", "Accept": "application/json, text/event-stream",
                               "Mcp-Protocol-Version": "2025-11-25", "Origin": "https://mcp.test"}
                    init = await a.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                        "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"}}})
                    assert init.status_code == 200
                    assert "mcp-session-id" not in init.headers
                    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                        "name": "get_connection_status", "arguments": {"service": "drive"}}}
                    alice = await b.post("/mcp", headers=headers, json=body)
                    assert alice.status_code == 200
                    assert "google-alice" in alice.text and "private-alice" not in alice.text
                    headers["Authorization"] = "Bearer bob"
                    bob = await a.post("/mcp", headers=headers, json=body)
                    assert "google-bob" in bob.text and "google-alice" not in bob.text
                    assert "private-bob" not in bob.text
                    headers["Authorization"] = "Bearer invalid"
                    assert (await a.post("/mcp", headers=headers, json=body)).status_code == 401
    store.engine.dispose()
