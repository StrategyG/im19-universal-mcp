"""Isolated Codespaces demo stack. Never modifies .env or production resources."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import secrets
# Trusted operations CLI; no subprocess invocation is exposed through MCP.
import subprocess  # nosec B404
import shutil
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
import httpx

CALLBACK = 'https://chatgpt.com/connector_platform_oauth_redirect'
KEYCLOAK_IMAGE = 'quay.io/keycloak/keycloak:26.8.1'
STATE = '.im19-codespaces'


def origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
            or any(c.isspace() for c in value)):
        raise ValueError('https_origin_required')
    _ = parsed.port
    return value.rstrip('/')


def callback_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.netloc != 'chatgpt.com' or parsed.query or parsed.fragment
            or not (parsed.path == '/connector_platform_oauth_redirect'
                    or re.fullmatch(r'/connector/oauth/[A-Za-z0-9_-]+', parsed.path))):
        raise ValueError('exact_chatgpt_callback_required')
    return value


def codespace_origins() -> tuple[str, str]:
    name = os.environ.get('CODESPACE_NAME', '')
    domain = os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN', 'app.github.dev')
    if not re.fullmatch(r'[a-zA-Z0-9-]+', name) or not re.fullmatch(r'[a-zA-Z0-9.-]+', domain):
        raise ValueError('run_in_codespaces_or_supply_both_https_origins')
    return f'https://{name}-8000.{domain}', f'https://{name}-8080.{domain}'


def realm_config(mcp: str, issuer: str, credentials: dict, callback: str) -> dict:
    def client(name, secret, redirects):
        return {
            'clientId': name, 'secret': secret, 'enabled': True, 'protocol': 'openid-connect',
            'publicClient': False, 'clientAuthenticatorType': 'client-secret',
            'standardFlowEnabled': bool(redirects), 'directAccessGrantsEnabled': False,
            'serviceAccountsEnabled': False, 'implicitFlowEnabled': False,
            'redirectUris': redirects, 'webOrigins': [], 'defaultClientScopes': ['im19:read', 'profile', 'email'],
            'attributes': {'pkce.code.challenge.method': 'S256',
                           'exclude.issuer.from.auth.response': 'false'},
            'protocolMappers': [{
                'name': 'im19-resource', 'protocol': 'openid-connect',
                'protocolMapper': 'oidc-audience-mapper',
                'config': {'included.custom.audience': mcp + '/mcp',
                           'id.token.claim': 'false', 'access.token.claim': 'true',
                           'introspection.token.claim': 'true'},
            }],
        }
    return {
        'realm': 'im19', 'enabled': True, 'sslRequired': 'all', 'registrationAllowed': False,
        'resetPasswordAllowed': False, 'bruteForceProtected': True, 'failureFactor': 5,
        'waitIncrementSeconds': 60, 'maxFailureWaitSeconds': 900,
        'accessTokenLifespan': 300, 'ssoSessionIdleTimeout': 1800,
        'clientScopes': [{'name': 'im19:read', 'protocol': 'openid-connect',
                          'attributes': {'include.in.token.scope': 'true',
                                         'display.on.consent.screen': 'true'}},
            {'name': 'profile', 'protocol': 'openid-connect',
             'attributes': {'include.in.token.scope': 'true'}, 'protocolMappers': [
                 {'name': 'username', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-usermodel-property-mapper',
                  'config': {'user.attribute': 'username', 'claim.name': 'preferred_username',
                             'jsonType.label': 'String', 'id.token.claim': 'true',
                             'access.token.claim': 'true', 'userinfo.token.claim': 'true'}}]},
            {'name': 'email', 'protocol': 'openid-connect',
             'attributes': {'include.in.token.scope': 'true'}, 'protocolMappers': [
                 {'name': 'email', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-usermodel-property-mapper',
                  'config': {'user.attribute': 'email', 'claim.name': 'email', 'jsonType.label': 'String',
                             'id.token.claim': 'true', 'access.token.claim': 'true', 'userinfo.token.claim': 'true'}}]},
        ],
        'clients': [
            client('im19-resource', credentials['introspection_secret'], []),
            client('im19-browser', credentials['browser_secret'], [mcp + '/oauth/browser/callback']),
            client('im19-chatgpt', credentials['chatgpt_secret'], [callback]),
        ],
        'users': [{'username': 'im19', 'enabled': True, 'firstName': 'IM19', 'lastName': 'Developer',
                   'email': 'im19@example.invalid', 'emailVerified': True, 'requiredActions': [],
                   'credentials': [{'type': 'password', 'value': credentials['login_password'],
                                    'temporary': False}]}],
    }


def configure(root: Path, mcp: str, issuer: str, callback: str = CALLBACK,
              mcp_port: int = 8000, issuer_port: int = 8080) -> dict:
    mcp, issuer, callback = origin(mcp), origin(issuer), callback_url(callback)
    if mcp == issuer or mcp_port == issuer_port or not all(1 <= p <= 65535 for p in (mcp_port, issuer_port)):
        raise ValueError('distinct_origins_and_valid_distinct_ports_required')
    directory = root / STATE
    if directory.is_symlink():
        raise ValueError('state_directory_must_not_be_symlink')
    settings = {'mcp_origin': mcp, 'issuer_origin': issuer, 'callback': callback,
                'mcp_port': mcp_port, 'issuer_port': issuer_port}
    if directory.exists():
        current = json.loads((directory / 'settings.json').read_text())
        if current != settings:
            raise ValueError('existing_stack_configuration_differs_no_secrets_or_volumes_changed')
        return settings
    directory.mkdir(mode=0o700)
    credentials = {name: secrets.token_hex(32) for name in (
        'database_password', 'introspection_secret', 'browser_secret', 'chatgpt_secret', 'login_password', 'admin_password')}
    auth = issuer + '/realms/im19'
    env = {
        'POSTGRES_PASSWORD': credentials['database_password'],
        'KC_BOOTSTRAP_ADMIN_PASSWORD': credentials['admin_password'],
        'IM19_DEPLOYMENT_ENV': 'staging',
        'IM19_DATABASE_URL': f"postgresql+psycopg://im19:{credentials['database_password']}@postgres:5432/im19",
        'IM19_FERNET_KEY': Fernet.generate_key().decode(), 'IM19_AUDIT_SALT': secrets.token_hex(32),
        'IM19_MCP_ISSUER': auth, 'IM19_MCP_RESOURCE': mcp + '/mcp',
        'IM19_MCP_INTROSPECTION': auth + '/protocol/openid-connect/token/introspect',
        'IM19_MCP_CLIENT_ID': 'im19-resource', 'IM19_MCP_CLIENT_SECRET': credentials['introspection_secret'],
        'IM19_BROWSER_AUTHORIZATION': auth + '/protocol/openid-connect/auth',
        'IM19_BROWSER_TOKEN': auth + '/protocol/openid-connect/token',
        'IM19_BROWSER_CLIENT_ID': 'im19-browser', 'IM19_BROWSER_CLIENT_SECRET': credentials['browser_secret'],
        'IM19_DEV_SUBJECT': '', 'IM19_FERNET_PREVIOUS_KEYS': '', 'IM19_PORT': '8000',
    }
    # Container bind only: published host ports are restricted to loopback.
    env['IM19_BIND_HOST'] = '0.0.0.0'  # nosec B104
    for field in ('IM19_GOOGLE_CLIENT_ID', 'IM19_GOOGLE_CLIENT_SECRET', 'IM19_GOOGLE_REDIRECT_URI'):
        env[field] = ''
    # This names a private container mount, not a host temporary file.
    container_tmpfs = ['/tmp']  # nosec B108
    project = 'im19cs-' + hashlib.sha256(mcp.encode()).hexdigest()[:12]
    gateway_env = {key: '${' + key + '}' for key in env if key.startswith('IM19_')}
    stack = {
        'name': project,
        'services': {
            'postgres': {'image': 'postgres:16-alpine', 'environment': {
                'POSTGRES_USER': 'im19', 'POSTGRES_DB': 'im19', 'POSTGRES_PASSWORD': '${' + 'POSTGRES_PASSWORD' + '}'},
                'volumes': ['pgdata:/var/lib/postgresql/data'], 'healthcheck': {
                    'test': ['CMD-SHELL', 'pg_isready -U im19 -d im19'], 'interval': '3s',
                    'timeout': '3s', 'retries': 30}},
            'keycloak': {'image': KEYCLOAK_IMAGE, 'command': ['start-dev', '--import-realm'],
                'environment': {'KC_HOSTNAME': issuer, 'KC_HTTP_ENABLED': 'true',
                    'KC_BOOTSTRAP_ADMIN_USERNAME': 'im19-ops',
                    'KC_BOOTSTRAP_ADMIN_PASSWORD': '${' + 'KC_BOOTSTRAP_ADMIN_PASSWORD' + '}',
                    'KC_PROXY_HEADERS': 'xforwarded',
                    'KC_PROXY_TRUSTED_ADDRESSES': '127.0.0.1,::1,172.16.0.0/12,192.168.0.0/16',
                    'JAVA_OPTS_KC_HEAP': '-Xms128m -Xmx512m'},
                'ports': [f'127.0.0.1:{issuer_port}:8080'],
                'volumes': ['kcdata:/opt/keycloak/data',
                            str(directory / 'im19-realm.json') + ':/opt/keycloak/data/import/im19-realm.json:ro'],
                'mem_limit': '1g'},
            'gateway': {'build': {'context': str(root)}, 'environment': gateway_env,
                'ports': [f'127.0.0.1:{mcp_port}:8000'],
                'depends_on': {'postgres': {'condition': 'service_healthy'}},
                'read_only': True, 'tmpfs': container_tmpfs, 'cap_drop': ['ALL'],
                'security_opt': ['no-new-privileges:true'], 'mem_limit': '512m'},
        }, 'volumes': {'pgdata': {}, 'kcdata': {}},
    }
    def write(name, value, mode=0o600):
        path = directory / name
        with path.open('x') as handle:
            os.chmod(path, mode)
            handle.write(value)
    write('settings.json', json.dumps(settings, indent=2))
    write('stack.env', ''.join(f'{key}={value}\n' for key, value in env.items()))
    write('compose.json', json.dumps(stack, indent=2))
    # Mounted FILE is readable by the container UID. Its private 0700 host
    # parent prevents other host users from traversing to this secret file.
    write('im19-realm.json', json.dumps(realm_config(mcp, issuer, credentials, callback)), 0o444)
    write('connection.json', json.dumps({'server_url': mcp + '/mcp', 'authentication': 'OAuth',
        'client_id': 'im19-chatgpt', 'client_secret': credentials['chatgpt_secret'],
        'login_username': 'im19', 'login_password': credentials['login_password'],
        'callback_url': callback, 'issuer_admin_username': 'im19-ops',
        'issuer_admin_password': credentials['admin_password']}, indent=2))
    return settings


def compose(root: Path, *args) -> None:
    directory = root / STATE
    executable = shutil.which('docker')
    if executable is None:
        raise RuntimeError('docker_required')
    # No shell; executable resolved once and subcommands supplied by trusted CLI code.
    result = subprocess.run([executable, 'compose', '--env-file', str(directory / 'stack.env'),  # nosec B603
        '-f', str(directory / 'compose.json'), *args], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        env={key: value for key, value in os.environ.items()
             if not key.startswith(('IM19_', 'KC_')) and key not in {
                 'POSTGRES_PASSWORD', 'COMPOSE_PROJECT_NAME', 'COMPOSE_PROFILES'}})
    if result.returncode:
        log = directory / 'setup.log'
        with log.open('w') as handle:
            os.chmod(log, 0o600)
            handle.write(result.stdout + '\n' + result.stderr)
        raise RuntimeError('docker_compose_failed_check_Docker_and_local_container_logs')


def local_checks(settings: dict, timeout: float = 120) -> None:
    end = time.monotonic() + timeout
    with httpx.Client(timeout=3, trust_env=False) as client:
        while time.monotonic() < end:
            try:
                auth = client.get(f"http://127.0.0.1:{settings['issuer_port']}/realms/im19/.well-known/openid-configuration",
                    headers={'Host': urlsplit(settings['issuer_origin']).netloc, 'X-Forwarded-Proto': 'https'})
                ready = client.get(f"http://127.0.0.1:{settings['mcp_port']}/ready",
                    headers={'Host': urlsplit(settings['mcp_origin']).netloc})
                if (auth.status_code == ready.status_code == 200 and
                        auth.json().get('issuer') == settings['issuer_origin'] + '/realms/im19'):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(2)
    raise RuntimeError('local_stack_not_ready_check_local_container_logs')


def verify_public(settings: dict) -> None:
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        auth = client.get(settings['issuer_origin'] + '/realms/im19/.well-known/openid-configuration')
        ready = client.get(settings['mcp_origin'] + '/ready')
        denied = client.get(settings['mcp_origin'] + '/mcp')
        if (auth.status_code != 200 or auth.json().get('issuer') != settings['issuer_origin'] + '/realms/im19'
                or ready.status_code != 200 or denied.status_code != 401):
            raise RuntimeError('public_endpoints_not_verified_check_Port_visibility_and_OAuth')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Isolated IM19 Codespaces OAuth demo; not production hosting')
    parser.add_argument('action', choices=['configure', 'up', 'verify-public', 'down'])
    parser.add_argument('--mcp-origin')
    parser.add_argument('--issuer-origin')
    parser.add_argument('--chatgpt-callback', default=CALLBACK)
    parser.add_argument('--mcp-port', type=int, default=8000)
    parser.add_argument('--issuer-port', type=int, default=8080)
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    if not (root / 'Dockerfile').is_file():
        parser.error('run_from_repository_root')
    phase = 'configuration'
    try:
        if args.action in {'configure', 'up'}:
            if bool(args.mcp_origin) != bool(args.issuer_origin):
                raise ValueError('supply_both_https_origins')
            mcp, issuer = (args.mcp_origin, args.issuer_origin) if args.mcp_origin else codespace_origins()
            settings = configure(root, mcp, issuer, args.chatgpt_callback, args.mcp_port, args.issuer_port)
        else:
            settings = json.loads((root / STATE / 'settings.json').read_text())
        if args.action == 'up':
            phase = 'start_database_and_issuer'
            print('Starting isolated PostgreSQL and Keycloak...', flush=True)
            compose(root, 'up', '-d', '--build', 'postgres', 'keycloak')
            compose(root, 'up', '-d', '--wait', '--wait-timeout', '120', 'postgres')
            phase = 'build_gateway'
            print('Building gateway...', flush=True)
            compose(root, 'build', 'gateway')
            phase = 'migrations'
            compose(root, 'run', '--rm', '--no-deps', 'gateway', 'alembic', 'upgrade', 'head')
            phase = 'remote_doctor'
            compose(root, 'run', '--rm', '--no-deps', 'gateway', 'im19-mcp-doctor', '--mode', 'remote', '--check-database')
            phase = 'gateway_start'
            compose(root, 'up', '-d', 'gateway')
            phase = 'local_readiness'
            print('Checking database and issuer readiness...', flush=True)
            local_checks(settings)
            # Execute the checkout's synthetic browser-login probe before asking
            # the operator to expose either authenticated endpoint.
            phase = 'oauth_login_probe'
            print('Testing real OAuth login and PKCE...', flush=True)
            probe = runpy.run_path(str(root / 'scripts/codespaces/probe_keycloak.py'))['probe']
            probe(root)
        elif args.action == 'down':
            compose(root, 'down')  # deliberately keep both volumes and all keys
        elif args.action == 'verify-public':
            verify_public(settings)
        print(json.dumps({'status': 'OK', 'action': args.action,
            'server_url': settings['mcp_origin'] + '/mcp',
            'local_credentials_file': STATE + '/connection.json',
            'public_access_verified': args.action == 'verify-public',
            'chatgpt_connected_verified': False, 'production_acceptance': False}))
        return 0
    except (OSError, ValueError, RuntimeError, AssertionError, httpx.HTTPError) as exc:
        controlled = {'login_page_unavailable', 'login_form_missing', 'login_not_completed',
                      'code_exchange_failed', 'code_replay_not_rejected', 'issuer_identification_missing'}
        reason = str(exc) if isinstance(exc, AssertionError) and str(exc) in controlled else type(exc).__name__
        print(json.dumps({'status': 'SETUP_FAILED', 'phase': phase, 'reason': reason, 'hint': 'Check Docker, origins, state consistency and local logs. No secret values printed.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
