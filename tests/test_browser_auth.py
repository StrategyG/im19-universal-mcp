"""Real encrypted state and mocked IdP/Google: proof precedes provider consent."""
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.server.auth.provider import AccessToken
from sqlalchemy import select

from im19_mcp.browser_auth import BrowserAuthorization, browser_digest
from im19_mcp.google_oauth import GoogleOAuth, OAuthError
from im19_mcp.storage import PendingState, Vault


def query(url, key):
    return parse_qs(urlsplit(url).query)[key][0]


class Verifier:
    def __init__(self, cfg, subject="alice", client_id=None):
        self.cfg, self.subject = cfg, subject
        self.client_id = client_id or cfg.browser_client_id
        self.calls = []

    async def verify_token(self, token):
        self.calls.append(token)
        return AccessToken(token=token, client_id=self.client_id, subject=self.subject,
                           resource=self.cfg.mcp_resource, scopes=["im19:read"],
                           expires_at=int(time.time()) + 300)


@pytest.fixture
def vault(remote_cfg):
    store = Vault(remote_cfg.database_url, encryption_key=remote_cfg.vault_key,
                  audit_salt=remote_cfg.audit_salt)
    store.initialize_schema()
    yield store
    store.engine.dispose()


@pytest.mark.asyncio
async def test_complete_browser_proof_google_consent_and_same_subject_confirmation(remote_cfg, vault):
    requests = []

    def reply(request):
        requests.append(request)
        if request.url.host == "identity.test":
            return httpx.Response(200, json={"access_token": "idp-ephemeral", "token_type": "Bearer"})
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "google-private", "expires_in": 3600,
                "scope": "openid https://www.googleapis.com/auth/drive.metadata.readonly"})
        return httpx.Response(200, json={"sub": "google-account-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        oauth = GoogleOAuth(remote_cfg, vault, client)
        verifier = Verifier(remote_cfg)
        browser = BrowserAuthorization(remote_cfg, vault, oauth, verifier, client)
        ticket = browser.ticket("alice", "drive")
        assert ticket["authorization_url"].startswith("https://mcp.test/connect/google?")
        login_url, cookie = await browser.start(query(ticket["authorization_url"], "ticket"))
        assert query(login_url, "code_challenge_method") == "S256"
        assert query(login_url, "resource") == remote_cfg.mcp_resource
        assert query(login_url, "redirect_uri") == "https://mcp.test/oauth/browser/callback"
        google = await browser.complete(state=query(login_url, "state"), code="idp-code", cookie=cookie)
        assert verifier.calls == ["idp-ephemeral"]
        assert b"code_verifier=" in requests[0].content
        state = query(google["authorization_url"], "state")
        pending = await oauth.complete(state=state, code="google-code", browser_hash=browser_digest(cookie))
        assert vault.get_credential("alice", "drive") is None
        assert oauth.confirm("alice", "drive", pending["confirmation_code"])["status"] == "CONNECTED"
        assert vault.get_credential("alice", "drive")[0] == "google-account-1"
        with vault.sessions() as session:
            assert not session.execute(select(PendingState)).all()


@pytest.mark.asyncio
@pytest.mark.parametrize("subject,client_id", [("attacker", None), ("alice", "foreign-client")])
async def test_ticket_does_not_authenticate_a_different_browser_user(remote_cfg, vault, subject, client_id):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"access_token": "idp-test", "token_type": "Bearer"},
    ))) as client:
        oauth = GoogleOAuth(remote_cfg, vault, client)
        browser = BrowserAuthorization(remote_cfg, vault, oauth,
            Verifier(remote_cfg, subject=subject, client_id=client_id), client)
        ticket = browser.ticket("alice", "youtube")
        login, cookie = await browser.start(query(ticket["authorization_url"], "ticket"))
        with pytest.raises(OAuthError, match="browser_identity_mismatch"):
            await browser.complete(state=query(login, "state"), code="code", cookie=cookie)
        assert vault.get_credential("alice", "youtube") is None
        with vault.sessions() as session:
            assert not session.execute(select(PendingState)).all()


@pytest.mark.asyncio
async def test_browser_cookie_and_flow_replay_are_rejected(remote_cfg, vault):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
        browser = BrowserAuthorization(remote_cfg, vault, GoogleOAuth(remote_cfg, vault), Verifier(remote_cfg), client)
        ticket = query(browser.ticket("alice", "drive")["authorization_url"], "ticket")
        login, cookie = await browser.start(ticket)
        with pytest.raises(PermissionError):
            await browser.start(ticket)
        with pytest.raises(OAuthError, match="browser_session_required"):
            await browser.complete(state=query(login, "state"), code="code", cookie="")
        with pytest.raises(PermissionError):
            await browser.complete(state=query(login, "state"), code="code", cookie="x" * 43)
        # A mismatched callback consumes the one-time state and never calls IdP.
        with pytest.raises(PermissionError):
            await browser.complete(state=query(login, "state"), code="code", cookie=cookie)
    assert calls == []


def test_production_refuses_legacy_google_linking_and_partial_browser_config(remote_cfg):
    production = replace(remote_cfg, deployment_environment="production",
                         database_url="postgresql+psycopg://user:test@db/im19")
    production.require_remote()
    with pytest.raises(ValueError, match="production_requires_authenticated_browser_linking"):
        replace(production, browser_authorization="", browser_token="", browser_client_id="",
                browser_client_secret="").require_remote()
    with pytest.raises(ValueError, match="browser_oauth_configuration_incomplete"):
        replace(production, browser_token="").require_remote()


@pytest.mark.parametrize("field", ["mcp_issuer", "mcp_resource", "mcp_introspection", "browser_token"])
def test_remote_endpoints_cannot_embed_query_tokens(remote_cfg, field):
    with pytest.raises(ValueError):
        replace(remote_cfg, **{field: "https://identity.test/path?secret=value"}).require_remote()


def test_configuration_repr_never_exposes_secrets(remote_cfg):
    rendered = repr(remote_cfg)
    for secret in (remote_cfg.database_url, remote_cfg.vault_key, remote_cfg.audit_salt,
                   remote_cfg.google_client_secret, remote_cfg.mcp_client_secret,
                   remote_cfg.browser_client_secret):
        assert secret not in rendered
