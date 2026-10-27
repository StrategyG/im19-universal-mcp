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

## Diagnose ChatGPT MCP initialization failures

Update/install the current checkout first; the Python module is `im19_mcp`:

```bash
git pull --ff-only origin main
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
python -m im19_mcp.codespaces verify-public
```

Public verification now checks issuer endpoints/PKCE, exact MCP resource and
issuer discovery, DB readiness and the POST authentication challenge. A GitHub
login redirect, wrong URL or missing OAuth discovery fails with a fixed reason.
Use the `server_url` ending in `/mcp` from local connection.json; port 8080 is
only the issuer. Set BOTH forwarded ports Public. No repository visibility
change is necessary.

To test authenticated MCP initialization, tools/list and a harmless local
connection-status invocation, obtain a short-lived access token through the
registered Authorization Code + PKCE flow. A client secret, login password or
Google token is NOT an MCP access token. Enter the token without echo/history:

```bash
read -rsp 'Short-lived MCP access token: ' IM19_MCP_TEST_TOKEN; echo
export IM19_MCP_TEST_TOKEN
python -m im19_mcp.codespaces verify-mcp
unset IM19_MCP_TEST_TOKEN
```

Never paste tokens into chat. The probe prints only fixed failure reasons and
acceptance flags. It does not send provider requests, authenticate ChatGPT on
your behalf, or establish production acceptance. `mcp_protocol_verified: true`
is independent from `chatgpt_connected_verified`, which remains false until a
real ChatGPT invocation is observed. OAuth login failures still need separate
issuer/callback investigation. Do not delete state or volumes to resolve an
initialization error; that would lose persisted keys/accounts.

## Write approval and browser permission (optional staging-only)

The generated Codespaces stack includes `IM19_ENABLE_APPROVED_DRIVE_WRITES=false`.
It must remain disabled until a Google OAuth client with the separate
`drive.file` consent is configured and the security review is complete.

In approved staging, IM19 first returns `APPROVAL_REQUIRED` with a URL to
`/approve/write`. Open that link on Android or desktop, complete the IM19
browser OAuth login, review the exact folder name, and select **Setujui** or
**Tolak**. The grant stays encrypted in PostgreSQL. The AI re-invokes
`drive_create_folder` using the returned `approval_request_id` and the
same folder name; it never receives a ledger grant token.

Before enabling the flag, migrate to Alembic revision `0006_write_approvals`
and register the exact `/approve/write/callback` URI with Keycloak's
`im19-browser` client. **Existing Codespaces state is preserved by `up`**;
changing Python source does not rewrite an existing imported Keycloak realm,
`stack.env` or `compose.json`. Apply any required OAuth callback/environment
changes via controlled administration, keeping persistent keys and volumes.
A successful CI Keycloak probe does not verify live Google writes.
