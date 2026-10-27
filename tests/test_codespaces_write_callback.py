"""Non-destructive Codespaces write-callback reconciliation tests."""
import json

import httpx
import pytest

from im19_mcp.codespaces import STATE, sync_write_callback


@pytest.fixture
def existing(tmp_path):
    directory = tmp_path / STATE
    directory.mkdir()
    (directory / "connection.json").write_text(json.dumps({
        "issuer_admin_username": "dev-operator",
        "issuer_admin_password": "SYNTHETIC-PRIVATE-ADMIN-PASSWORD",
    }))
    settings = {
        "issuer_origin": "https://codespace-8080.app.github.dev",
        "mcp_origin": "https://codespace-8000.app.github.dev",
        "issuer_port": 8080,
    }
    return tmp_path, settings


def test_sync_preserves_redirects_does_not_reimport_realm_or_log_secrets(existing):
    root, cfg = existing
    preexisting = "https://codespace-8000.app.github.dev/oauth/browser/callback"
    new = "https://codespace-8000.app.github.dev/approve/write/callback"
    unrelated = "https://other.example.invalid/callback"
    redirects = [preexisting, unrelated]
    calls = []

    def provider(request: httpx.Request):
        calls.append((request.method, request.url.path))
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 8080
        assert request.headers["Host"] == "codespace-8080.app.github.dev"
        if request.url.path.endswith("/token"):
            assert b"SYNTHETIC-PRIVATE-ADMIN-PASSWORD" in request.content
            return httpx.Response(200, json={"access_token": "synthetic-admin-bearer-" + "a" * 32})
        assert request.headers["Authorization"].startswith("Bearer synthetic-admin-")
        if request.url.path.endswith("/clients") and request.method == "GET":
            assert request.url.params["clientId"] == "im19-browser"
            return httpx.Response(200, json=[{"clientId": "im19-browser", "id": "keycloak-uuid"}])
        if request.method == "PUT":
            body = json.loads(request.content)
            assert body["clientId"] == "im19-browser"
            assert body["unchanged"] == {"roles": ["example"]}
            redirects[:] = body["redirectUris"]
            return httpx.Response(204)
        return httpx.Response(200, json={
            "id": "keycloak-uuid", "clientId": "im19-browser",
            "standardFlowEnabled": True, "redirectUris": redirects,
            "unchanged": {"roles": ["example"]},
        })

    def client():
        return httpx.Client(transport=httpx.MockTransport(provider))

    with client() as h:
        assert sync_write_callback(root, cfg, client=h) == "REDIRECT_VERIFIED"
    assert redirects == [preexisting, unrelated, new]
    assert sum(m == "PUT" for m, _ in calls) == 1
    with client() as h:
        assert sync_write_callback(root, cfg, client=h) == "ALREADY_CONFIGURED"
    assert sum(m == "PUT" for m, _ in calls) == 1
    assert json.loads((root / STATE / "connection.json").read_text())["issuer_admin_password"] == (
        "SYNTHETIC-PRIVATE-ADMIN-PASSWORD"
    )


@pytest.mark.parametrize("redirects", [
    [],
    ["https://untrusted.example/callback"],
    ["https://codespace-8000.app.github.dev/approve/write/callback"],
    ["https://codespace-8000.app.github.dev/oauth/browser/callback", "*.example.com"],
])
def test_rejects_unexpected_browser_client_without_mutation(existing, redirects):
    root, cfg = existing
    methods = []

    def provider(request):
        methods.append(request.method)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "synthetic-token-" + "a" * 32})
        if request.url.path.endswith("/clients"):
            return httpx.Response(200, json=[{"clientId": "im19-browser", "id": "uuid"}])
        return httpx.Response(200, json={
            "clientId": "im19-browser",
            "standardFlowEnabled": True, "redirectUris": redirects,
        })

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        # Unexpected client configuration must not be silently widened.
        with pytest.raises(RuntimeError, match="unexpected_configuration"):
            sync_write_callback(root, cfg, client=client)
    assert "PUT" not in methods


def test_rejects_failed_admin_login_without_write(existing):
    root, cfg = existing
    def provider(request):
        return httpx.Response(401)
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises(RuntimeError, match="keycloak_admin_authentication_failed"):
            sync_write_callback(root, cfg, client=client)


def test_rejects_state_symlinks(existing, tmp_path):
    root, cfg = existing
    original = root / STATE / "connection.json"
    original.unlink()
    original.symlink_to(tmp_path / "untrusted-key.json")
    with pytest.raises(ValueError, match="codespaces_state_invalid"):
        sync_write_callback(root, cfg)
