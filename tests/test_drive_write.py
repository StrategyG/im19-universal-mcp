"""Approval-gated Drive write acceptance: tests use synthetic consent and provider."""
from dataclasses import replace
import json

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp import Client
from mcp.server import MCPServer

from im19_mcp.config import Settings
from im19_mcp.drive import DriveClient, validate_folder_name
from im19_mcp.google_oauth import SCOPES, GoogleOAuth, OAuthError
from im19_mcp.governance.approvals import ApprovalLedger, payload_sha256
from im19_mcp.server import register_tools
from im19_mcp.storage import Vault
from im19_mcp.youtube import ProviderError


def body(result):
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


class SyntheticOAuth:
    def __init__(self, *, consent=True):
        self.consent = consent
        self.calls = []

    async def access_token(self, user, scopes, *, service="youtube"):
        self.calls.append((user, service, scopes))
        if not self.consent or SCOPES["drive_write"][0] not in scopes:
            raise OAuthError("SCOPE_REQUIRED")
        return "google-account-A", "synthetic-access-token"


class SyntheticDrive:
    count = 0
    fail = False

    def __init__(self, access_token):
        assert access_token == "synthetic-access-token"

    async def create_folder(self, name):
        SyntheticDrive.count += 1
        if SyntheticDrive.fail:
            raise ProviderError("WRITE_OUTCOME_UNKNOWN")
        return {"id": "folder_ABC123", "name": name, "verified": True}


@pytest.fixture
def gov(tmp_path):
    vault = Vault("sqlite:///" + str(tmp_path / "write.sqlite3"),
                  encryption_key=Fernet.generate_key().decode(), audit_salt="test-audit-salt")
    vault.initialize_schema()
    return ApprovalLedger(vault)


def issue(ledger, name="Production Assets", *, subject="alice", account="google-account-A"):
    return ledger.issue(
        subject=subject, provider="drive", account_id=account,
        action="drive.create_folder", resource_id="root",
        payload_hash=payload_sha256({"name": name, "mimeType": "application/vnd.google-apps.folder"}),
        approver="verified-human-operator", evidence_reference="verified-review-12345",
    )


@pytest.mark.asyncio
async def test_mcp_write_is_disabled_by_default(monkeypatch, gov):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "alice")
    server = MCPServer("write off")
    register_tools(server, settings=Settings.from_env(), vault=gov.vault,
                   oauth=SyntheticOAuth(), allow_stdio_dev=True, drive_factory=SyntheticDrive)
    async with Client(server) as client:
        status = body(await client.call_tool("drive_create_folder", {
            "name": "Production Assets", "approval_token": issue(gov),
            "idempotency_key": "first-request-2026",
        }))
        assert status["status"] == "FEATURE_DISABLED"
    assert SyntheticDrive.count == 0


@pytest.mark.asyncio
async def test_mcp_write_needs_matching_human_approval_and_one_use_key(monkeypatch, gov):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "alice")
    SyntheticDrive.count = 0
    SyntheticDrive.fail = False
    server = MCPServer("approved write")
    auth = SyntheticOAuth()
    register_tools(server, settings=replace(Settings.from_env(), enable_approved_drive_writes=True),
                   vault=gov.vault, oauth=auth, allow_stdio_dev=True, drive_factory=SyntheticDrive)
    async with Client(server) as client:
        invalid = body(await client.call_tool("drive_create_folder", {
            "name": "Production Assets", "approval_token": issue(gov, name="Different"),
            "idempotency_key": "request-invalid-1",
        }))
        assert invalid["status"] == "APPROVAL_REQUIRED"
        assert SyntheticDrive.count == 0
        auth_missing = body(await client.call_tool("drive_create_folder", {"name": "Production Assets"}))
        assert auth_missing["status"] == "APPROVAL_REQUIRED"
        token = issue(gov)
        ok = body(await client.call_tool("drive_create_folder", {
            "name": "Production Assets", "approval_token": token,
            "idempotency_key": "request-approved-1",
        }))
        assert ok["status"] == "SUCCEEDED"
        assert ok["verified"] is True
        assert gov.get_receipt(subject="alice", reservation_id=ok["reservation_id"]).status == "SUCCEEDED"
        same = body(await client.call_tool("drive_create_folder", {
            "name": "Production Assets", "approval_token": issue(gov),
            "idempotency_key": "request-approved-1",
        }))
        assert same["status"] == "DUPLICATE_REQUEST"
    assert SyntheticDrive.count == 1
    assert auth.calls[0][1] == "drive"


@pytest.mark.asyncio
async def test_failed_external_write_is_not_retried_automatically(monkeypatch, gov):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "alice")
    SyntheticDrive.count = 0
    SyntheticDrive.fail = True
    server = MCPServer("uncertain write")
    register_tools(server, settings=replace(Settings.from_env(), enable_approved_drive_writes=True),
                   vault=gov.vault, oauth=SyntheticOAuth(),
                   allow_stdio_dev=True, drive_factory=SyntheticDrive)
    try:
        async with Client(server) as client:
            one = body(await client.call_tool("drive_create_folder", {
                "name": "Uncertain folder", "approval_token": issue(gov, "Uncertain folder"),
                "idempotency_key": "request-uncertain-1",
            }))
            assert one["status"] == "UNKNOWN_OUTCOME"
            assert gov.get_receipt(subject="alice", reservation_id=one["reservation_id"]).status == "RESERVED"
            two = body(await client.call_tool("drive_create_folder", {
                "name": "Uncertain folder", "approval_token": issue(gov, "Uncertain folder"),
                "idempotency_key": "request-uncertain-1",
            }))
            assert two["status"] == "DUPLICATE_REQUEST"
        assert SyntheticDrive.count == 1
    finally:
        SyntheticDrive.fail = False


@pytest.mark.asyncio
async def test_write_oauth_is_checked_before_approval_consumption(monkeypatch, gov):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "alice")
    token = issue(gov)
    server = MCPServer("scope denied")
    register_tools(server, settings=replace(Settings.from_env(), enable_approved_drive_writes=True),
                   vault=gov.vault, oauth=SyntheticOAuth(consent=False),
                   allow_stdio_dev=True, drive_factory=SyntheticDrive)
    async with Client(server) as client:
        denied = body(await client.call_tool("drive_create_folder", {
            "name": "Production Assets", "approval_token": token,
            "idempotency_key": "request-consent-denied",
        }))
        assert denied["status"] == "SCOPE_REQUIRED"
    # The consent failure did not consume a human approval.
    assert gov.reserve(token=token, subject="alice", provider="drive",
                       account_id="google-account-A", action="drive.create_folder",
                       resource_id="root", payload={"name": "Production Assets",
                       "mimeType": "application/vnd.google-apps.folder"},
                       idempotency_key="separate-manual-123").status == "RESERVED"


@pytest.mark.asyncio
async def test_provider_post_has_bounded_response_and_verifies_get_metadata():
    seen = []
    def reply(request):
        seen.append((request.method, request.url.path))
        assert request.url.host == "www.googleapis.com"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        if request.method == "POST":
            assert json.loads(request.content) == {
                "name": "Trusted Assets", "mimeType": "application/vnd.google-apps.folder"}
            return httpx.Response(200, json={"id": "folder_ABCD",
                "name": "Trusted Assets", "mimeType": "application/vnd.google-apps.folder"})
        return httpx.Response(200, json={
            "id": "folder_ABCD", "name": "Trusted Assets",
            "mimeType": "application/vnd.google-apps.folder"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        data = await DriveClient("synthetic-token", client).create_folder("Trusted Assets")
    assert data["verified"] is True
    assert [method for method, _ in seen] == ["POST", "GET"]


@pytest.mark.asyncio
async def test_provider_post_ambiguous_metadata_does_not_claim_success():
    def reply(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "folder_ABCD", "name": "Trusted Assets",
                                             "mimeType": "application/vnd.google-apps.folder"})
        return httpx.Response(200, json={"id": "folder_ABCD", "name": "Something else",
                                         "mimeType": "application/vnd.google-apps.folder"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(ProviderError, match="WRITE_VERIFICATION_FAILED"):
            await DriveClient("synthetic-token", client).create_folder("Trusted Assets")


@pytest.mark.parametrize("name", ["", " name", "name ", "bad/name", "name\\segment",
                                  "x" * 129, ".", "..", "line\nbreak"])
def test_unsafe_folder_names_rejected_before_write(name):
    with pytest.raises(ValueError):
        validate_folder_name(name)


def test_write_oauth_scope_is_explicit_opt_in():
    requested = GoogleOAuth.requested_scopes("drive")
    assert SCOPES["drive_write"][0] not in requested
    explicit = GoogleOAuth.requested_scopes("drive", include_write=True)
    assert SCOPES["drive_write"][0] in explicit
    with pytest.raises(ValueError):
        GoogleOAuth.requested_scopes("youtube", include_write=True)
