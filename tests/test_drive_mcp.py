"""MCP-level security regression: scope-gated Drive tools never borrow YouTube tokens."""
import json

import pytest
from mcp import Client
from mcp.server import MCPServer

from im19_mcp.config import Settings
from im19_mcp.google_oauth import OAuthError
from im19_mcp.server import register_tools


def payload(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


class FakeProviderOAuth:
    def __init__(self):
        self.calls = []

    def begin(self, subject, *, service, analytics=False, include_content=False):
        self.calls.append((subject, service, analytics, include_content))
        return {"status": "AUTH_REQUIRED", "service": service,
                "authorization_url": "https://accounts.google.com/consent"}

    def confirm(self, subject, service, confirmation_code):
        self.calls.append(("confirm", subject, service, confirmation_code))
        if subject != "mock-user-1" or service != "drive" or confirmation_code != "one-time-code":
            raise OAuthError("confirmation_invalid_or_expired")
        return {"status": "CONNECTED", "service": "drive", "account_id": "mock-account"}

    async def access_token(self, subject, scopes, *, service="youtube"):
        self.calls.append((subject, service, tuple(sorted(scopes))))
        if service != "drive":
            raise OAuthError("AUTH_REQUIRED")
        if any(scope.endswith("/drive.readonly") for scope in scopes):
            raise OAuthError("SCOPE_REQUIRED")
        return "google-account-mock", "mock-drive-token"


class FakeDrive:
    def __init__(self, access_token):
        assert access_token == "mock-drive-token"

    async def search_files(self, term, *, page_size=25, page_token=""):
        return {"files": [{"id": "file_1234", "name": term}]}


@pytest.mark.asyncio
async def test_mcp_drive_scope_policy_and_service_isolation(monkeypatch):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "mock-user-1")
    oauth = FakeProviderOAuth()
    server = MCPServer("IM19 security mock")
    register_tools(server, settings=Settings.from_env(), vault=None,
                   oauth=oauth, allow_stdio_dev=True, drive_factory=FakeDrive)
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"drive_search_files", "drive_list_folder", "drive_read_file",
                "drive_get_metadata"}.issubset(names)
        result = payload(await client.call_tool("drive_search_files", {"term": "ebook"}))
        assert result["service"] == "drive"
        assert result["data"]["files"][0]["name"] == "ebook"
        denied = payload(await client.call_tool("drive_read_file", {"file_id": "file_1234"}))
        assert denied["status"] == "SCOPE_REQUIRED"
        assert oauth.calls[0][1] == "drive"
        url = payload(await client.call_tool(
            "request_service_authorization", {"service": "drive", "include_content": False}))
        assert url["status"] == "AUTH_REQUIRED"
        assert oauth.calls[-1] == ("mock-user-1", "drive", False, False)
        confirmed = payload(await client.call_tool(
            "confirm_service_authorization",
            {"service": "drive", "confirmation_code": "one-time-code"},
        ))
        assert confirmed["status"] == "CONNECTED"
        assert oauth.calls[-1] == ("confirm", "mock-user-1", "drive", "one-time-code")
        rejected = payload(await client.call_tool(
            "confirm_service_authorization",
            {"service": "drive", "confirmation_code": "wrong-code"},
        ))
        assert rejected["status"] == "DENY"


@pytest.mark.asyncio
async def test_mcp_tools_never_accept_unverified_subject(monkeypatch):
    monkeypatch.delenv("IM19_DEV_SUBJECT", raising=False)
    server = MCPServer("IM19 no identity")
    register_tools(server, settings=Settings.from_env(), vault=None,
                   oauth=FakeProviderOAuth(), allow_stdio_dev=False)
    async with Client(server) as client:
        result = payload(await client.call_tool("drive_search_files", {"term": "secret"}))
        assert result["status"] == "AUTH_REQUIRED"
        result = payload(await client.call_tool(
            "get_connection_status", {"service": "drive"}))
        assert result["status"] == "AUTH_REQUIRED"
        confirm = payload(await client.call_tool(
            "confirm_service_authorization",
            {"service": "drive", "confirmation_code": "one-time-code"},
        ))
        assert confirm["status"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_connection_status_does_not_claim_live_google_verification(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from im19_mcp.storage import Vault
    import time

    monkeypatch.setenv("IM19_DEV_SUBJECT", "mock-user-1")
    vault = Vault(
        "sqlite:///" + str(tmp_path / "connected.sqlite3"),
        encryption_key=Fernet.generate_key().decode(), audit_salt="testing-salt",
    )
    vault.initialize_schema()
    vault.put_credential(
        "mock-user-1", "drive", "mock-google-account",
        {"access_token": "private-mock-token", "scopes": ["drive.metadata.readonly"]},
        time.time() + 3600,
    )
    server = MCPServer("IM19 connection status")
    register_tools(server, settings=Settings.from_env(), vault=vault,
                   oauth=FakeProviderOAuth(), allow_stdio_dev=True)
    async with Client(server) as client:
        response = payload(await client.call_tool("get_connection_status", {"service": "drive"}))
        assert response["status"] == "CREDENTIAL_STORED_NOT_LIVE_VERIFIED"
        assert response["provider_live_verified"] is False
        assert "private-mock-token" not in json.dumps(response)
