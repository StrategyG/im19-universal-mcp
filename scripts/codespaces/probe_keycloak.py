"""CI/local synthetic login probe. Never prints passwords, codes or tokens."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import hashlib
import html
import json
from pathlib import Path
import re
import secrets
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx

from im19_mcp.config import Settings
from im19_mcp.gateway import IntrospectionVerifier


def probe(root: Path) -> None:
    state = root / '.im19-codespaces'
    settings = json.loads((state / 'settings.json').read_text())
    connection = json.loads((state / 'connection.json').read_text())
    values = dict(line.split('=', 1) for line in (state / 'stack.env').read_text().splitlines() if '=' in line)
    public = settings['issuer_origin']
    loopback = f"http://127.0.0.1:{settings['issuer_port']}"

    class Forward(httpx.BaseTransport):
        def __init__(self):
            self.wire = httpx.HTTPTransport()

        def handle_request(self, request):
            original = request.url
            assert str(original).startswith(public + '/')
            request.url = httpx.URL(loopback + original.raw_path.decode())
            request.headers['X-Forwarded-Proto'] = 'https'
            try:
                return self.wire.handle_request(request)
            finally:
                request.url = original

        def close(self):
            self.wire.close()

    issuer = values['IM19_MCP_ISSUER']
    pkce = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(pkce.encode()).digest()).rstrip(b'=').decode()
    nonce = secrets.token_urlsafe(24)
    with httpx.Client(transport=Forward(), follow_redirects=False, timeout=15) as client:
        metadata = client.get(issuer + '/.well-known/openid-configuration').json()
        assert metadata['issuer'] == issuer
        assert metadata.get('authorization_response_iss_parameter_supported') is True, 'issuer_identification_missing'
        login = client.get(metadata['authorization_endpoint'], params={
            'client_id': connection['client_id'], 'response_type': 'code',
            'redirect_uri': settings['callback'], 'scope': 'openid profile email im19:read', 'state': nonce,
            'code_challenge': challenge, 'code_challenge_method': 'S256',
            'resource': values['IM19_MCP_RESOURCE'],
        })
        assert login.status_code == 200, 'login_page_unavailable'
        match = re.search(r'<form[^>]+action="([^"]+)"', login.text)
        assert match, 'login_form_missing'
        result = client.post(html.unescape(match.group(1)), data={
            'username': connection['login_username'], 'password': connection['login_password'],
            'credentialId': '',
        })
        assert result.status_code in {302, 303}, 'login_not_completed'
        location = result.headers['location']
        callback = urlsplit(location)
        expected = urlsplit(settings['callback'])
        assert (callback.scheme, callback.netloc, callback.path) == (expected.scheme, expected.netloc, expected.path)
        query = parse_qs(callback.query)
        assert query.get('state') == [nonce] and query.get('iss') == [issuer]
        code = query['code'][0]
        response = client.post(metadata['token_endpoint'], auth=(connection['client_id'], connection['client_secret']),
            data={'grant_type': 'authorization_code', 'code': code, 'redirect_uri': settings['callback'],
                  'code_verifier': pkce, 'resource': values['IM19_MCP_RESOURCE']})
        assert response.status_code == 200, 'code_exchange_failed'
        access = response.json()['access_token']

    class AsyncForward(httpx.AsyncBaseTransport):
        def __init__(self):
            self.wire = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            original = request.url
            assert str(original).startswith(public + '/')
            request.url = httpx.URL(loopback + original.raw_path.decode())
            request.headers['X-Forwarded-Proto'] = 'https'
            try:
                return await self.wire.handle_async_request(request)
            finally:
                request.url = original

        async def aclose(self):
            await self.wire.aclose()

    async def verify(revoked=False):
        cfg = Settings(database_url=values['IM19_DATABASE_URL'], vault_key=values['IM19_FERNET_KEY'],
            audit_salt=values['IM19_AUDIT_SALT'], google_client_id='', google_client_secret='', google_redirect_uri='',
            mcp_issuer=issuer, mcp_resource=values['IM19_MCP_RESOURCE'],
            mcp_introspection=values['IM19_MCP_INTROSPECTION'], mcp_client_id=values['IM19_MCP_CLIENT_ID'],
            mcp_client_secret=values['IM19_MCP_CLIENT_SECRET'], dev_subject='', deployment_environment='staging')
        real = httpx.AsyncClient
        def factory(**kwargs):
            return real(transport=AsyncForward(), timeout=10)
        with patch('im19_mcp.gateway.httpx.AsyncClient', side_effect=factory):
            identity = await IntrospectionVerifier(cfg).verify_token(access)
            if revoked:
                assert identity is None, 'replayed_session_token_still_active'
                return
            assert identity and identity.subject and identity.client_id == 'im19-chatgpt', 'valid_token_not_verified'
            assert 'im19:read' in identity.scopes
            assert await IntrospectionVerifier(replace(cfg, mcp_resource='https://foreign.test/mcp')).verify_token(access) is None
            assert await IntrospectionVerifier(cfg).verify_token('invalid-synthetic-token') is None
    asyncio.run(verify())
    # Keycloak invalidates the code's user session after replay. Verify the
    # valid grant FIRST, then require replay and its old token to be rejected.
    with httpx.Client(transport=Forward(), follow_redirects=False, timeout=15) as client:
        replay = client.post(metadata['token_endpoint'], auth=(connection['client_id'], connection['client_secret']),
            data={'grant_type': 'authorization_code', 'code': code, 'redirect_uri': settings['callback'],
                  'code_verifier': pkce})
        assert replay.status_code == 400, 'code_replay_not_rejected'
    asyncio.run(verify(revoked=True))
    print('PASS: real Keycloak login, PKCE code exchange/replay, issuer, client, scope and audience introspection')


if __name__ == '__main__':
    probe(Path.cwd())
