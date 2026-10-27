"""Deployment acceptance probes reject login walls and broken MCP responses."""
import httpx
import pytest

from im19_mcp.codespaces import verify_mcp, verify_public

SETTINGS = {'mcp_origin': 'https://mcp.test', 'issuer_origin': 'https://identity.test'}


def public_response(request):
    path = request.url.path
    if path.endswith('openid-configuration'):
        return httpx.Response(200, json={'issuer': 'https://identity.test/realms/im19',
            'authorization_endpoint': 'https://identity.test/realms/im19/auth',
            'token_endpoint': 'https://identity.test/realms/im19/token', 'code_challenge_methods_supported': ['S256']})
    if path.startswith('/.well-known/'):
        return httpx.Response(200, json={'resource': 'https://mcp.test/mcp',
            'authorization_servers': ['https://identity.test/realms/im19'], 'scopes_supported': ['im19:read']})
    if path == '/ready':
        return httpx.Response(200)
    return httpx.Response(401, headers={'www-authenticate':
        'Bearer resource_metadata="https://mcp.test/.well-known/oauth-protected-resource/mcp"'})


def install_client(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr('im19_mcp.codespaces.httpx.Client',
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_public_discovery_and_post_challenge(monkeypatch):
    install_client(monkeypatch, public_response)
    verify_public(SETTINGS)


@pytest.mark.parametrize('failure', ['login_wall', 'wrong_resource', 'missing_challenge'])
def test_public_probe_rejects_inaccessible_or_incompatible_endpoint(monkeypatch, failure):
    def handler(request):
        if failure == 'login_wall':
            return httpx.Response(302, headers={'location': 'https://github.com/login'})
        if failure == 'wrong_resource' and request.url.path.startswith('/.well-known/oauth-protected'):
            return httpx.Response(200, json={'resource': 'https://wrong.test/mcp'})
        if failure == 'missing_challenge' and request.method == 'POST':
            return httpx.Response(401)
        return public_response(request)
    install_client(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        verify_public(SETTINGS)


@pytest.mark.parametrize('failure', [None, 'initialize', 'schema', 'call', 'invalid_json', 'unauthorized'])
def test_authenticated_protocol_acceptance(monkeypatch, failure):
    methods = []
    def handler(request):
        import json
        assert request.headers['authorization'] == 'Bearer synthetic-only'
        body = json.loads(request.content)
        method = body['method']
        methods.append(method)
        if failure == 'unauthorized':
            return httpx.Response(401)
        if failure == 'invalid_json':
            return httpx.Response(200, text='<html>login</html>')
        if method == 'notifications/initialized':
            return httpx.Response(202)
        result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}
        if method == 'initialize' and failure == 'initialize':
            result = {}
        if method == 'tools/list':
            result = {'tools': [{'name': 'get_connection_status', 'inputSchema': {'type': 'object'}}]}
            if failure == 'schema':
                result['tools'][0]['inputSchema'] = 'invalid'
        if method == 'tools/call':
            assert body['params'] == {'name': 'get_connection_status', 'arguments': {'service': 'drive'}}
            result = {'content': [{'type': 'text', 'text': '{"status":"NOT_CONNECTED"}'}],
                      'isError': failure == 'call'}
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})
    install_client(monkeypatch, handler)
    if failure:
        with pytest.raises(RuntimeError):
            verify_mcp(SETTINGS, 'synthetic-only')
    else:
        verify_mcp(SETTINGS, 'synthetic-only')
        assert methods == ['initialize', 'notifications/initialized', 'tools/list', 'tools/call']


def test_missing_token_never_sends_a_request(monkeypatch):
    monkeypatch.setattr('im19_mcp.codespaces.httpx.Client', lambda **kw: pytest.fail('must not connect'))
    with pytest.raises(RuntimeError, match='mcp_test_token_required'):
        verify_mcp(SETTINGS, '')
