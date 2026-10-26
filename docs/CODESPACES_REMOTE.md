# Codespaces: configure the IM19 plugin test stack

This is a **development/demo environment**, with a pinned Keycloak 26.8.1
start-dev issuer and a PostgreSQL 16 gateway database. It does not change your
existing .env, start production services or claim ChatGPT is connected.
Keycloak's development H2 database and gateway PostgreSQL use separate named
volumes. No anonymous mode, fake OAuth issuer or token bypass is introduced.

## Start from the repository root

```bash
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
python -m im19_mcp.codespaces up
```

Docker/Compose must be installed and the daemon running. The command derives
public hostnames from the actual Codespaces environment, creates random secrets
and an im19 login account, imports distinct introspection/browser/ChatGPT OAuth
clients, starts the services, migrates the gateway DB, runs the remote doctor,
waits for PostgreSQL health, checks local readiness, and performs a real synthetic browser login with PKCE,
code replay rejection and gateway audience/scope/issuer verification. It reports
failure instead of claiming success if any step fails. First pull/build may take
several minutes. It does not automatically change port visibility.

All local setup state is in `.im19-codespaces/`, ignored by git AND Docker build
context. The directory is mode 0700 and secret files are 0600. The imported
realm file is readable inside the container but protected by its private host
parent. No credentials are printed. A separate random temporary issuer admin account
(im19-ops) is also recorded in connection.json for controlled local administration.
It is not the ChatGPT login account. Never paste its files into chat, commit them,
run `docker compose config` in shared output, or share container environment dumps.
The helper ignores stale IM19/POSTGRES_PASSWORD shell variables from earlier
attempts, using its own generated stack.env explicitly.

Repeated `up` preserves keys, accounts, DBs and client secrets. A different
Codespace hostname/callback/port configuration is refused rather than silently
rotating credentials or replacing a realm. Resolve any existing local setup
conflict before retrying. `down` stops the stack without deleting volumes.
The existing .env and existing compose.yaml volumes are never modified.

## Expose and verify authenticated endpoints

After `up` succeeds, open the Codespaces **Ports** panel. Forward ports **8000**
(gateway) and **8080** (issuer) and set visibility to **Public** for this demo.
Both host bindings remain loopback; Codespaces provides external HTTPS. Do not
expose PostgreSQL. Organization policies may restrict public forwarding.

Then run:

```bash
python -m im19_mcp.codespaces verify-public
```

This checks public issuer discovery, gateway readiness and a 401 response to an
unauthenticated /mcp request, with redirects disabled. A private port/sign-in wall
must fail verification. This does not itself prove a live ChatGPT invocation.

## Add the plugin

1. Open `.im19-codespaces/connection.json` **locally in the editor**.
2. In ChatGPT web Plugins, add a custom MCP server using `server_url` and OAuth.
3. Supply the **im19-chatgpt** client_id and client_secret from that file. These
   are different from the internal resource introspection and browser clients.
4. Complete the issuer login using login_username/login_password from that file.
5. Install/select IM19 and test `list_available_services`.

The exact callback shown by ChatGPT must match the registered callback. This
setup uses https://chatgpt.com/connector_platform_oauth_redirect with issuer
identification (RFC 9207), verified by the real issuer probe. If ChatGPT shows a
callback-specific URL, supply its **exact** value using `--chatgpt-callback URL`
before the first configure/up. No wildcard callback or foreign host is accepted.
Do not reset an existing realm or discard keys just to change a callback; plan a
controlled configuration change if the stack already contains data.

Google credentials are disabled by default so registry discovery/plugin login
can be tested independently. Google Drive/YouTube tools report NOT_CONFIGURED
until separately configured; this stack does not grant Google access. The realm
uses an explicit fixed audience mapper for this one MCP resource. It is not a
multi-resource authorization service or a production identity deployment.

Codespaces suspension stops service availability. For continuous production,
use proper hosting, a production issuer/database/TLS configuration and complete
the acceptance controls in PRODUCTION_READINESS.md. No production acceptance or
successful Android/ChatGPT installation is claimed by the bootstrap.

## Stop the demo

```bash
python -m im19_mcp.codespaces down
```

## Sources

- https://www.keycloak.org/getting-started/getting-started-docker
- https://www.keycloak.org/server/importExport
- https://www.keycloak.org/server/hostname
- https://developers.openai.com/plugins/build/auth
