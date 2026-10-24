import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet

from im19_mcp.config import Settings
from im19_mcp.google_oauth import GoogleOAuth, OAuthError
from im19_mcp.storage import Vault


@pytest.fixture
def cfg(tmp_path):
    return Settings(
        database_url="sqlite:///" + str(tmp_path / "db.sqlite3"),
        vault_key=Fernet.generate_key().decode(), audit_salt="salt",
        google_client_id="example.apps.googleusercontent.com", google_client_secret="client-secret",
        google_redirect_uri="https://example.test/oauth/google/callback",
        mcp_issuer="", mcp_resource="", mcp_introspection="",
        mcp_client_id="", mcp_client_secret="", dev_subject="",
    )


@pytest.fixture
def vault(cfg):
    vault = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt)
    vault.initialize_schema()
    return vault


def test_consent_url_pkce_least_privilege(cfg, vault):
    auth = GoogleOAuth(cfg, vault)
    result = auth.begin("subject1")
    params = parse_qs(urlsplit(result["authorization_url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert len(params["state"][0]) > 20
    assert "openid" in params["scope"][0]
    assert "youtube.readonly" in params["scope"][0]
    assert "yt-analytics.readonly" not in params["scope"][0]


def test_analytics_scope_requested_only_explicitly(cfg, vault):
    auth = GoogleOAuth(cfg, vault)
    result = auth.begin("subject1", analytics=True)
    assert "yt-analytics.readonly" in parse_qs(urlsplit(result["authorization_url"]).query)["scope"][0]


@pytest.mark.asyncio
async def test_mocked_callback_refresh_and_replay(cfg, vault):
    requests = []
    def fake(request: httpx.Request):
        requests.append(request)
        if str(request.url).endswith("/token"):
            if b"grant_type=refresh_token" in request.content:
                return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})
            return httpx.Response(200, json={
                "access_token": "mock-access", "refresh_token": "mock-refresh", "expires_in": 3600,
                "scope": "openid https://www.googleapis.com/auth/youtube.readonly",
            })
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json={"sub": "google-account-1"})
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
        auth = GoogleOAuth(cfg, vault, http_client=client)
        state = parse_qs(urlsplit(auth.begin("subject1")["authorization_url"]).query)["state"][0]
        result = await auth.complete(state=state, code="test-code")
        assert result["status"] == "CONNECTED"
        assert result["account_id"] == "google-account-1"
        with pytest.raises(PermissionError):
            await auth.complete(state=state, code="test-code")
        account, access = await auth.access_token(
            "subject1", {"https://www.googleapis.com/auth/youtube.readonly"})
        assert account == "google-account-1" and access == "mock-access"
        stored = vault.get_credential("subject1", "youtube")
        vault.put_credential("subject1", "youtube", "google-account-1", stored[1], time.time() - 2)
        assert (await auth.access_token(
            "subject1", {"https://www.googleapis.com/auth/youtube.readonly"}))[1] == "refreshed"
        assert vault.get_credential("subject2", "youtube") is None
    assert any(b"code_verifier" in r.content for r in requests)


@pytest.mark.asyncio
async def test_mocked_denied_scope(cfg, vault):
    def fake(request):
        return httpx.Response(200, json={"access_token": "access", "expires_in": 3600, "scope": "openid"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
        auth = GoogleOAuth(cfg, vault, http_client=client)
        state = parse_qs(urlsplit(auth.begin("u")["authorization_url"]).query)["state"][0]
        with pytest.raises(OAuthError, match="google_scope_insufficient"):
            await auth.complete(state=state, code="test-code")
        assert vault.get_credential("u", "youtube") is None


def test_drive_consent_is_separate_and_metadata_only_by_default(cfg, vault):
    auth = GoogleOAuth(cfg, vault)
    youtube = auth.begin("user1")
    drive = auth.begin("user1", service="drive")
    yt_scopes = parse_qs(urlsplit(youtube["authorization_url"]).query)["scope"][0]
    drive_scopes = parse_qs(urlsplit(drive["authorization_url"]).query)["scope"][0]
    assert "youtube.readonly" in yt_scopes
    assert "drive.metadata.readonly" in drive_scopes
    assert "youtube.readonly" not in drive_scopes
    assert "/drive.readonly" not in drive_scopes


def test_drive_content_scope_never_requested_without_explicit_option(cfg, vault):
    auth = GoogleOAuth(cfg, vault)
    consent = auth.begin("user1", service="drive", include_content=True)
    scopes = parse_qs(urlsplit(consent["authorization_url"]).query)["scope"][0]
    assert "https://www.googleapis.com/auth/drive.readonly" in scopes
    with pytest.raises(ValueError, match="google_scope_option_invalid"):
        auth.begin("user1", service="youtube", include_content=True)
    with pytest.raises(ValueError, match="google_scope_option_invalid"):
        auth.begin("user1", service="drive", analytics=True)


@pytest.mark.asyncio
async def test_google_drive_oauth_stored_in_independent_service_vault(cfg, vault):
    def fake(request: httpx.Request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={
                "access_token": "drive-mock-access",
                "refresh_token": "drive-mock-refresh",
                "expires_in": 3600,
                "scope": "openid https://www.googleapis.com/auth/drive.metadata.readonly",
            })
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json={"sub": "google-account-1"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
        oauth = GoogleOAuth(cfg, vault, http_client=client)
        state = parse_qs(urlsplit(
            oauth.begin("user1", service="drive")["authorization_url"]
        ).query)["state"][0]
        data = await oauth.complete(state=state, code="mock-code")
        assert data["service"] == "drive"
        assert vault.get_credential("user1", "youtube") is None
        assert vault.get_credential("user2", "drive") is None
        assert vault.get_credential("user1", "drive")[1]["access_token"] == "drive-mock-access"
        with pytest.raises(OAuthError, match="SCOPE_REQUIRED"):
            await oauth.access_token(
                "user1",
                {"https://www.googleapis.com/auth/drive.readonly"},
                service="drive",
            )
