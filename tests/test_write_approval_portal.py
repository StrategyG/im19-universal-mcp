"""Write approval requires browser OAuth proof, explicit human decision and server-held grant.

These are mocked identity-provider/Google tests. No live write is claimed.
"""
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp.server.auth.provider import AccessToken

from im19_mcp.config import Settings
from im19_mcp.governance.approvals import ApprovalLedger
from im19_mcp.governance.write_approval import WriteApprovalPortal
from im19_mcp.storage import Vault


class FakeVerifier:
    def __init__(self, subject="alice", client_id="im19-browser"):
        self.subject, self.client_id = subject, client_id

    async def verify_token(self, token):
        assert token == "synthetic-browser-token"
        return AccessToken(token=token, subject=self.subject, client_id=self.client_id,
                           resource="https://mcp.test/mcp", scopes=["im19:read"],
                           expires_at=2_000_000_000)


@pytest.fixture
def data(tmp_path):
    settings = Settings(
        database_url="sqlite:///" + str(tmp_path / "approval.sqlite"),
        vault_key=Fernet.generate_key().decode(),
        audit_salt="synthetic-test-audit-salt",
        google_client_id="synthetic", google_client_secret="synthetic",
        google_redirect_uri="https://mcp.test/oauth/google/callback",
        mcp_issuer="https://identity.test/realms/im19",
        mcp_resource="https://mcp.test/mcp",
        mcp_introspection="https://identity.test/realms/im19/protocol/openid-connect/token/introspect",
        mcp_client_id="im19-resource", mcp_client_secret="synthetic-secret",
        dev_subject="", deployment_environment="staging",
        browser_authorization="https://identity.test/realms/im19/protocol/openid-connect/auth",
        browser_token="https://identity.test/realms/im19/protocol/openid-connect/token",
        browser_client_id="im19-browser", browser_client_secret="synthetic-secret",
        enable_approved_drive_writes=True,
    )
    vault = Vault(settings.database_url, encryption_key=settings.vault_key,
                  audit_salt=settings.audit_salt)
    vault.initialize_schema()
    return settings, vault


def fake_token(request):
    assert request.method == "POST"
    assert request.url.path.endswith("/token")
    assert b"code_verifier=" in request.content
    assert b"resource=" in request.content
    return httpx.Response(200, json={"access_token": "synthetic-browser-token",
                                     "token_type": "Bearer"})


@pytest.fixture
async def portal(data):
    cfg, vault = data
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_token)) as http_client:
        yield WriteApprovalPortal(cfg, vault, FakeVerifier(), http_client)


@pytest.mark.asyncio
async def test_explicit_oauth_review_approval_and_backend_only_token(portal, data):
    cfg, vault = data
    pending = portal.request(subject="alice", account_id="google-account-A",
                             name="Approved Assets")
    assert pending["status"] == "APPROVAL_REQUIRED"
    assert "/approve/write?" in pending["approval_url"]
    ticket = pending["approval_request_id"]
    login, cookie = portal.begin(ticket)
    args = parse_qs(urlsplit(login).query)
    assert args["code_challenge_method"] == ["S256"]
    assert args["redirect_uri"] == ["https://mcp.test/approve/write/callback"]
    key, csrf, name = await portal.callback(
        state=args["state"][0], code="synthetic-code", cookie=cookie)
    assert name == "Approved Assets"
    markup = portal.review_html(request_key=key, csrf=csrf, name='<img onerror="alert(1)">')
    assert "<img onerror=" not in markup
    assert "&lt;img" in markup
    assert "Setujui" in markup and "Tolak" in markup
    # Model/request identifier is not enough to issue approval or to read grant.
    with pytest.raises(PermissionError):
        portal.consume(ticket=ticket, subject="alice", account_id="google-account-A",
                       name="Approved Assets")
    assert portal.decide(request_key=key, cookie=cookie, csrf=csrf, decision="approve") == "APPROVED"
    with pytest.raises(PermissionError):
        portal.decide(request_key=key, cookie=cookie, csrf=csrf, decision="approve")
    grant, idem = portal.consume(ticket=ticket, subject="alice",
                                 account_id="google-account-A", name="Approved Assets")
    assert len(grant) >= 32 and idem.startswith("write-")
    with pytest.raises(PermissionError):
        portal.consume(ticket=ticket, subject="alice", account_id="google-account-A",
                       name="Approved Assets")
    receipt = ApprovalLedger(vault).reserve(
        token=grant, subject="alice", provider="drive", account_id="google-account-A",
        action="drive.create_folder", resource_id="root",
        payload={"name": "Approved Assets", "mimeType": "application/vnd.google-apps.folder"},
        idempotency_key=idem,
    )
    assert receipt.status == "RESERVED"


@pytest.mark.asyncio
async def test_wrong_browser_subject_and_cookie_never_approves(data):
    cfg, vault = data
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_token)) as c:
        portal = WriteApprovalPortal(cfg, vault, FakeVerifier(subject="attacker"), c)
        pending = portal.request(subject="alice", account_id="google-account-A", name="Assets")
        login, cookie = portal.begin(pending["approval_request_id"])
        state = parse_qs(urlsplit(login).query)["state"][0]
        with pytest.raises(PermissionError):
            await portal.callback(state=state, code="synthetic", cookie="x" * 43)
        # Mismatched browser proof can never redeem the same login again.
        with pytest.raises(PermissionError):
            await portal.callback(state=state, code="synthetic", cookie=cookie)
        pending2 = portal.request(subject="alice", account_id="google-account-A", name="Assets")
        login2, cookie2 = portal.begin(pending2["approval_request_id"])
        state2 = parse_qs(urlsplit(login2).query)["state"][0]
        with pytest.raises(PermissionError, match="subject_mismatch"):
            await portal.callback(state=state2, code="synthetic", cookie=cookie2)


@pytest.mark.asyncio
async def test_user_can_deny_without_creating_any_approval(portal, data):
    _cfg, vault = data
    pending = portal.request(subject="alice", account_id="a", name="Declined")
    login, cookie = portal.begin(pending["approval_request_id"])
    key, csrf, _name = await portal.callback(
        state=parse_qs(urlsplit(login).query)["state"][0], code="synthetic", cookie=cookie)
    assert portal.decide(request_key=key, cookie=cookie, csrf=csrf, decision="deny") == "DENIED"
    with pytest.raises(PermissionError, match="denied"):
        portal.consume(ticket=pending["approval_request_id"], subject="alice",
                       account_id="a", name="Declined")
    with vault.sessions() as session:
        from im19_mcp.governance.approvals import ApprovalGrant
        assert session.query(ApprovalGrant).count() == 0


@pytest.mark.asyncio
async def test_approval_bound_to_exact_account_subject_and_payload(portal):
    pending = portal.request(subject="alice", account_id="google-A", name="Assets")
    login, cookie = portal.begin(pending["approval_request_id"])
    key, csrf, _ = await portal.callback(
        state=parse_qs(urlsplit(login).query)["state"][0], code="synthetic", cookie=cookie)
    with pytest.raises(PermissionError):
        portal.decide(request_key=key, cookie=cookie, csrf="wrong-" + csrf, decision="approve")
    portal.decide(request_key=key, cookie=cookie, csrf=csrf, decision="approve")
    for wrong in [
        {"subject": "attacker", "account_id": "google-A", "name": "Assets"},
        {"subject": "alice", "account_id": "google-B", "name": "Assets"},
        {"subject": "alice", "account_id": "google-A", "name": "Other"},
    ]:
        with pytest.raises(PermissionError):
            portal.consume(ticket=pending["approval_request_id"], **wrong)
    grant, _ = portal.consume(ticket=pending["approval_request_id"],
                              subject="alice", account_id="google-A", name="Assets")
    assert grant


@pytest.mark.asyncio
async def test_browser_approval_routes_and_origin_protection(data, monkeypatch):
    from unittest.mock import AsyncMock
    from im19_mcp.gateway import create_app, IntrospectionVerifier

    cfg, _vault = data
    monkeypatch.setattr("im19_mcp.governance.write_approval.request_json",
                        AsyncMock(return_value={"access_token": "synthetic-browser-token",
                                               "token_type": "Bearer"}))
    monkeypatch.setattr(IntrospectionVerifier, "verify_token",
                        FakeVerifier().verify_token)
    app = create_app(cfg)
    # Manually bootstrap local SQLite schema for isolated route testing.
    db = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt)
    db.initialize_schema()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://mcp.test",
        follow_redirects=False,
    ) as client:
        assert (await client.get("/approve/write")).status_code == 400
        assert (await client.post("/approve/write/decision", headers={"Origin": "https://evil.test"},
                                  data={"decision": "approve"})).status_code == 403
        # Create via trusted service to test route; the browser URL itself is NOT an approval.
        p = WriteApprovalPortal(cfg, db, FakeVerifier())
        pending = p.request(subject="alice", account_id="google-A", name="Preview")
        start = await client.get("/approve/write", params={"ticket": pending["approval_request_id"]})
        assert start.status_code == 303
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        callback = await client.get("/approve/write/callback",
                                    params={"state": state, "code": "synthetic-code"})
        assert callback.status_code == 200
        assert "form-action 'self'" in callback.headers["Content-Security-Policy"]
        assert "Preview" in callback.text
        import re
        request_key = re.search(r"name='request' value='([^']+)'", callback.text).group(1)
        csrf = re.search(r"name='csrf' value='([^']+)'", callback.text).group(1)
        bad = await client.post("/approve/write/decision", headers={"Origin": "https://mcp.test"},
                                data={"request": request_key, "csrf": "bad", "decision": "approve"})
        assert bad.status_code == 403
        good = await client.post("/approve/write/decision", headers={"Origin": "https://mcp.test"},
                                 data={"request": request_key, "csrf": csrf, "decision": "approve"})
        assert good.status_code == 200
        assert "Persetujuan tersimpan" in good.text
