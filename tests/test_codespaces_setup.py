"""Bootstrap tests use isolated synthetic state; no real accounts or keys."""
import importlib.util
import json
from pathlib import Path
import stat

import pytest

from im19_mcp.codespaces import callback_url, codespace_origins, configure, realm_config, STATE
from im19_mcp.config import Settings


def test_generation_is_private_idempotent_and_preserves_existing_env(tmp_path, monkeypatch):
    original = tmp_path / '.env'
    original.write_text('KEEP_ME=untouched\n')
    configure(tmp_path, 'https://mcp.test', 'https://identity.test')
    state = tmp_path / STATE
    saved = (state / 'stack.env').read_bytes()
    configure(tmp_path, 'https://mcp.test', 'https://identity.test')
    assert (state / 'stack.env').read_bytes() == saved
    assert original.read_text() == 'KEEP_ME=untouched\n'
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / 'stack.env').stat().st_mode) == 0o600
    for line in saved.decode().splitlines():
        key, value = line.split('=', 1)
        monkeypatch.setenv(key, value)
    cfg = Settings.from_env()
    cfg.require_remote()
    assert not cfg.google_enabled() and cfg.browser_enabled()
    stack = json.loads((state / 'compose.json').read_text())
    assert set(stack['services']) == {'postgres', 'keycloak', 'gateway'}
    assert 'ports' not in stack['services']['postgres']
    for service in ('gateway', 'keycloak'):
        assert stack['services'][service]['ports'][0].startswith('127.0.0.1:')
    assert cfg.mcp_issuer == 'https://identity.test/realms/im19'


def test_existing_state_mismatch_never_rotates_keys(tmp_path):
    configure(tmp_path, 'https://mcp.test', 'https://identity.test')
    path = tmp_path / STATE / 'stack.env'
    old = path.read_bytes()
    with pytest.raises(ValueError, match='existing_stack_configuration_differs'):
        configure(tmp_path, 'https://other.test', 'https://identity.test')
    assert path.read_bytes() == old


@pytest.mark.parametrize('bad', ['https://evil.test/callback', 'http://chatgpt.com/connector_platform_oauth_redirect',
                                'https://chatgpt.com/connector/oauth/*', 'https://chatgpt.com/connector/oauth/x?q=secret'])
def test_callback_registration_never_uses_a_wildcard_or_foreign_host(bad):
    with pytest.raises(ValueError):
        callback_url(bad)


def test_realm_uses_distinct_clients_pkce_and_exact_audience():
    credentials = {k: k + '-synthetic-only' for k in (
        'introspection_secret', 'browser_secret', 'chatgpt_secret', 'login_password')}
    realm = realm_config('https://mcp.test', 'https://identity.test', credentials,
                         'https://chatgpt.com/connector/oauth/specific_id')
    assert realm['registrationAllowed'] is False
    assert realm['bruteForceProtected'] is True
    clients = {c['clientId']: c for c in realm['clients']}
    assert clients['im19-resource']['redirectUris'] == []
    for client in clients.values():
        assert not client['directAccessGrantsEnabled'] and not client['publicClient']
        assert client['attributes']['pkce.code.challenge.method'] == 'S256'
        assert client['protocolMappers'][0]['config']['included.custom.audience'] == 'https://mcp.test/mcp'
        assert client['protocolMappers'][0]['config']['included.client.audience'] == 'im19-resource'
    assert clients['im19-browser']['redirectUris'] == ['https://mcp.test/oauth/browser/callback']
    assert clients['im19-chatgpt']['redirectUris'] == ['https://chatgpt.com/connector/oauth/specific_id']


def test_codespace_detection_does_not_guess_an_unknown_hostname(monkeypatch):
    monkeypatch.delenv('CODESPACE_NAME', raising=False)
    with pytest.raises(ValueError):
        codespace_origins()
    monkeypatch.setenv('CODESPACE_NAME', 'demo-codespace')
    monkeypatch.setenv('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN', 'app.github.dev')
    assert codespace_origins() == ('https://demo-codespace-8000.app.github.dev',
                                   'https://demo-codespace-8080.app.github.dev')


def test_compose_does_not_inherit_stale_secret_variables(tmp_path, monkeypatch):
    from im19_mcp.codespaces import compose
    from types import SimpleNamespace
    monkeypatch.setenv('IM19_MCP_ISSUER', 'https://placeholder.invalid')
    monkeypatch.setenv('POSTGRES_PASSWORD', 'wrong-stale-password')
    captured = {}
    def run(command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('im19_mcp.codespaces.shutil.which', lambda _: '/usr/bin/docker')
    monkeypatch.setattr('im19_mcp.codespaces.subprocess.run', run)
    compose(tmp_path, 'up', '-d')
    assert 'IM19_MCP_ISSUER' not in captured['env']
    assert 'POSTGRES_PASSWORD' not in captured['env']


def test_generated_credentials_are_excluded_from_git_and_docker_context():
    root = Path(__file__).resolve().parents[1]
    assert '.im19-codespaces/' in (root / '.gitignore').read_text()
    assert '.im19-codespaces' in (root / '.dockerignore').read_text()
    spec = importlib.util.spec_from_file_location('probe', root / 'scripts/codespaces/probe_keycloak.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.probe)


def test_cli_failure_reports_phase_without_leaking_backend_messages(tmp_path, monkeypatch, capsys):
    from im19_mcp.codespaces import main
    (tmp_path / 'Dockerfile').touch()
    monkeypatch.chdir(tmp_path)
    def fail(*args):
        raise RuntimeError('SYNTHETIC-SECRET-DO-NOT-PRINT')
    monkeypatch.setattr('im19_mcp.codespaces.compose', fail)
    assert main(['up', '--mcp-origin', 'https://mcp.test', '--issuer-origin', 'https://identity.test']) == 1
    output = capsys.readouterr().out
    assert 'start_database_and_issuer' in output
    assert 'SYNTHETIC-SECRET-DO-NOT-PRINT' not in output
