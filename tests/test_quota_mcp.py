"""MCP client quota tests: identities come from verified context/dev harness only.

The local dev subject below is an explicit test-only fixture. Remote gateway
never enables this fallback.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from mcp import Client
from mcp.server import MCPServer
from sqlalchemy import text

from im19_mcp.config import Settings
from im19_mcp.distributed_quota import DistributedQuota
from im19_mcp.server import register_tools
from im19_mcp.storage import Vault


def payload(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


class StubOAuth:
    def __init__(self):
        self.calls = []

    def begin(self, subject, *, service, analytics=False, include_content=False):
        self.calls.append(("begin", subject, service))
        return {"status": "AUTH_URL_READY"}

    def confirm(self, subject, service, confirmation_code):
        self.calls.append(("confirm", subject, service))
        return {"status": "CONNECTED"}

    async def access_token(self, subject, scopes, *, service="youtube"):
        self.calls.append(("access", subject, service))
        return "mock-account", "mock-token"


class StubDrive:
    def __init__(self, token):
        assert token == "mock-token"

    async def search_files(self, term, page_size=25, page_token=""):
        return {"files": [{"id": "mock-id", "name": term}]}


@pytest.fixture
def ledger(tmp_path):
    vault = Vault(
        "sqlite:///" + str(tmp_path / "mcp-quotas.sqlite3"),
        encryption_key=Fernet.generate_key().decode(),
        audit_salt="test-audit-salt",
    )
    vault.initialize_schema()
    return DistributedQuota(vault.engine, secret="synthetic-strong-test-salt")


@pytest.mark.asyncio
async def test_user_oauth_consent_limit_prevents_state_spam(ledger, monkeypatch):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "verified-only-in-test")
    fake = StubOAuth()
    server = MCPServer("IM19 quota test")
    register_tools(
        server, settings=Settings.from_env(), vault=None, oauth=fake,
        allow_stdio_dev=True, distributed_quota=ledger,
    )
    async with Client(server) as client:
        for _ in range(5):
            result = payload(await client.call_tool(
                "request_service_authorization", {"service": "drive"},
            ))
            assert result["status"] == "AUTH_URL_READY"
        blocked = payload(await client.call_tool(
            "request_service_authorization", {"service": "drive"},
        ))
        assert blocked["status"] == "RATE_LIMITED"
        assert 1 <= blocked["retry_after_seconds"] <= 60
        assert len(fake.calls) == 5
        # Quotas are scoped to a verified identity, not an untrusted
        # service name or model-supplied account ID.
        monkeypatch.setenv("IM19_DEV_SUBJECT", "another-test-user")
        other = payload(await client.call_tool(
            "request_service_authorization", {"service": "drive"},
        ))
        assert other["status"] == "AUTH_URL_READY"


@pytest.mark.asyncio
async def test_user_read_quota_before_provider_access(ledger, monkeypatch):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "user-reads-test")
    for _ in range(60):
        assert ledger.consume("mcp:provider-reads", "user-reads-test", limit=60)[0]
    fake = StubOAuth()
    server = MCPServer("quota reads")
    register_tools(
        server, settings=Settings.from_env(), vault=None, oauth=fake,
        allow_stdio_dev=True, distributed_quota=ledger, drive_factory=StubDrive,
    )
    async with Client(server) as client:
        blocked = payload(await client.call_tool("drive_search_files", {"term": "confidential"}))
        assert blocked["status"] == "RATE_LIMITED"
        assert not fake.calls
        monkeypatch.setenv("IM19_DEV_SUBJECT", "user-different")
        allowed = payload(await client.call_tool("drive_search_files", {"term": "public"}))
        assert allowed["status"] == "OK"
        assert fake.calls[0][:2] == ("access", "user-different")


@pytest.mark.asyncio
async def test_quota_database_outage_fail_closed_before_oauth(ledger, monkeypatch):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "user-a")
    with ledger.engine.begin() as conn:
        conn.execute(text("DROP TABLE distributed_rate_windows"))
    fake = StubOAuth()
    server = MCPServer("no rate ledger")
    register_tools(
        server, settings=Settings.from_env(), vault=None, oauth=fake,
        allow_stdio_dev=True, distributed_quota=ledger,
    )
    async with Client(server) as client:
        denied = payload(await client.call_tool(
            "request_service_authorization", {"service": "drive"},
        ))
        assert denied == {
            "status": "SERVICE_UNAVAILABLE", "reason": "quota_backend_unavailable"
        }
        assert fake.calls == []


@pytest.mark.asyncio
async def test_missing_identity_cannot_charge_or_bypass_quota(ledger, monkeypatch):
    monkeypatch.delenv("IM19_DEV_SUBJECT", raising=False)
    server = MCPServer("no user")
    register_tools(
        server, settings=Settings.from_env(), vault=None, oauth=StubOAuth(),
        allow_stdio_dev=False, distributed_quota=ledger,
    )
    async with Client(server) as client:
        response = payload(await client.call_tool(
            "confirm_service_authorization", {
                "service": "drive", "confirmation_code": "not-a-real-code",
            },
        ))
        assert response["status"] == "AUTH_REQUIRED"
    with ledger.sessions() as session:
        from sqlalchemy import select
        from im19_mcp.distributed_quota import RateWindow
        assert session.execute(select(RateWindow)).all() == []
