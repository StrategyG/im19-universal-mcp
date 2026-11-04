"""Live acceptance smoke is intentionally read-only and must not overclaim."""
import httpx
import pytest

from im19_mcp.release_check import run_smoke

RESOURCE = "https://mcp.test/mcp"
ISSUER = "https://identity.test/realms/im19"


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.headers.get("host") == "invalid.example":
        return httpx.Response(421, json={"error": "INVALID_HOST"})
    if request.headers.get("origin") == "https://invalid.example":
        return httpx.Response(403, json={"error": "INVALID_ORIGIN"})
    if path == "/.well-known/oauth-protected-resource/mcp":
        return httpx.Response(200, json={
            "resource": RESOURCE, "authorization_servers": [ISSUER],
            "scopes_supported": ["im19:read"],
        })
    if path == "/realms/im19/.well-known/openid-configuration":
        return httpx.Response(200, json={
            "issuer": ISSUER,
            "authorization_endpoint": ISSUER + "/protocol/openid-connect/auth",
            "token_endpoint": ISSUER + "/protocol/openid-connect/token",
            "code_challenge_methods_supported": ["S256"],
        })
    if path == "/ready":
        return httpx.Response(200, json={"status": "ready"})
    if path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if path == "/mcp":
        if request.headers.get("authorization") != "Bearer synthetic-read-token":
            return httpx.Response(401, headers={
                "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.test/.well-known/oauth-protected-resource/mcp"',
            })
        import json
        rpc = json.loads(request.content)
        method = rpc["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        result = {
            "initialize": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}},
            "tools/list": {"tools": [{"name": "get_connection_status",
                                     "inputSchema": {"type": "object"}}]},
            "tools/call": {"content": [{"type": "text", "text": '{"status":"NOT_CONNECTED"}'}],
                           "isError": False},
        }[method]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result})
    return httpx.Response(404)


def test_public_only_does_not_claim_authenticated_or_production():
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as c:
        report = run_smoke(RESOURCE, ISSUER, client=c)
    assert report["status"] == "PUBLIC_PREFLIGHT_ONLY"
    assert report["checks"]["host_origin_denial_verified"]
    assert report["authenticated_tested"] is False
    assert report["chatgpt_connected_verified"] is False
    assert report["production_acceptance"] is False


def test_full_live_mcp_protocol_smoke_with_synthetic_token():
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as c:
        report = run_smoke(RESOURCE, ISSUER, token="synthetic-read-token", client=c)
    assert report["status"] == "SMOKE_PASSED"
    assert report["checks"]["authenticated_tool_discovery_verified"]
    assert report["checks"]["read_only_mcp_tool_verified"]
    assert report["backup_restore_verified"] is False
    assert report["production_acceptance"] is False
    assert "synthetic-read-token" not in str(report)


@pytest.mark.parametrize("failure", [
    "resource_mismatch", "issuer_mismatch", "pkce_missing", "not_ready",
    "no_challenge", "bad_host_denial", "bad_origin_denial",
])
def test_public_security_contract_fail_closed(failure):
    def failing(request):
        response = handler(request)
        path = request.url.path
        if failure == "resource_mismatch" and path.endswith("/oauth-protected-resource/mcp"):
            return httpx.Response(200, json={"resource": "https://wrong.test/mcp",
                       "authorization_servers": [ISSUER], "scopes_supported": ["im19:read"]})
        if failure == "issuer_mismatch" and path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": "https://wrong.test"})
        if failure == "pkce_missing" and path.endswith("openid-configuration"):
            p = response.json()
            p["code_challenge_methods_supported"] = ["plain"]
            return httpx.Response(200, json=p)
        if failure == "not_ready" and path == "/ready":
            return httpx.Response(503)
        if failure == "no_challenge" and path == "/mcp":
            return httpx.Response(401)
        if failure == "bad_host_denial" and request.headers.get("host") == "invalid.example":
            return httpx.Response(200)
        if failure == "bad_origin_denial" and request.headers.get("origin") == "https://invalid.example":
            return httpx.Response(200)
        return response

    with httpx.Client(transport=httpx.MockTransport(failing), follow_redirects=False) as c:
        report = run_smoke(RESOURCE, ISSUER, client=c)
    assert report["status"] == "SMOKE_FAILED"
    assert report["production_acceptance"] is False


@pytest.mark.parametrize("resource,issuer", [
    ("http://mcp.test/mcp", ISSUER),
    ("https://user:pass@mcp.test/mcp", ISSUER),
    ("https://mcp.test/mcp?token=topsecret", ISSUER),
    ("https://mcp.test/other", ISSUER),
    (RESOURCE, "http://identity.test"),
])
def test_invalid_urls_cannot_initiate_requests(resource, issuer):
    with httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail("do not connect"))) as c:
        with pytest.raises(ValueError):
            run_smoke(resource, issuer, client=c)


def test_authentication_failures_do_not_disclose_test_token():
    def denied(request):
        if request.url.path == "/mcp" and request.headers.get("authorization"):
            return httpx.Response(403, text="token synthetic-read-token invalid")
        return handler(request)

    with httpx.Client(transport=httpx.MockTransport(denied), follow_redirects=False) as c:
        report = run_smoke(RESOURCE, ISSUER, token="synthetic-read-token", client=c)
    assert report["status"] == "SMOKE_FAILED"
    assert "synthetic-read-token" not in str(report)
