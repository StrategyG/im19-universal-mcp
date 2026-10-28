# IM19 Universal MCP — Intelligent Mind 19

**Status: 0.3.0 read-only gateway baseline; an opt-in, approval-gated Drive folder creation prototype is under review (not a production write release).** Includes authenticated browser linking, encrypted provider credentials, shared quotas and transactional audit. See the [production readiness report](docs/PRODUCTION_READINESS.md) for executed checks and remaining deployment acceptance. No live Google, YouTube or ChatGPT connection is claimed.

## Architecture

```text
ChatGPT (remote OAuth MCP client)
   -> IM19 authenticated Streamable HTTP gateway
      -> MCP tool registry and server-side subject
      -> encrypted OAuth vault (PostgreSQL/SQLAlchemy)
      -> first-party browser Authorization Code + PKCE, same-subject proof
      -> Google OAuth Authorization Code + PKCE, browser-bound callback
      -> official YouTube Data v3 / YouTube Analytics v2 (read-only)
      -> official Google Drive v3 metadata, opt-in text extraction (read-only)
```

The ChatGPT-to-MCP OAuth issuer and the Google OAuth provider are **two separate authorization relationships**. Google account consent cannot replace authentication to the MCP gateway.

## Implemented tools

- `list_available_services`, `get_service_capabilities`, `get_connection_status` (local credential status only; **not a live Google API verification**).
- `request_service_authorization` (Google consent URL for an authenticated MCP subject).
- `confirm_service_authorization` (explicit same-subject, short-lived one-time code confirmation; callback alone cannot link an account).
- `disconnect_service` (explicitly declines automated revocation; requires a separately authenticated user portal).
- `youtube_get_channel`, `youtube_list_videos`, `youtube_get_analytics` (read-only, require valid stored provider tokens and respective scopes).
- `drive_search_files`, `drive_list_folder`, `drive_get_metadata` (read-only, separate Drive OAuth and metadata scope).
- `drive_read_file` (bounded text only, requires **explicit restricted drive.readonly** consent; unsupported MIME and binary/PDF/EPUB return an error).
- `drive_create_folder` (**disabled by default**; asks the user to approve in a browser after IM19 OAuth login, with separate Drive `drive.file` consent, server-held one-time approval, idempotency reservation and read-after-write verification; [limitations](docs/DRIVE_WRITE.md)).

Other providers are registered as **not implemented**, not falsely advertised as connected.

## Optional browser worker foundation

An experimental, read-only Chromium inspector is available as a separately
installed `browser` extra. It is **not exposed as an MCP tool**, cannot log in
to connected accounts, and requires a dedicated network-isolated worker before
production use. See [browser worker security and usage](docs/BROWSER_WORKER.md).

## Codespaces plugin connection setup

For an isolated PostgreSQL + Keycloak OAuth test environment, use the
[Codespaces remote setup](docs/CODESPACES_REMOTE.md). Run `python -m im19_mcp.codespaces up`
from the repository root after installation. It generates secrets locally, starts
the services, migrates the DB and checks real issuer login before public exposure.
This is a demo environment; no production or ChatGPT connectivity is assumed.

## Development run

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install -e '.[dev]'
python -m pytest -q
python -m compileall -q src tests
im19-mcp
```

Local `stdio` mode requires no Google credentials for tool discovery. If `IM19_DEV_SUBJECT` is set, it represents a **trusted local-development identity only**; remote mode never uses it.

## Colab CPU development

Use the [reviewed Colab notebook](colab/IM19_Universal_MCP_Colab.ipynb) and
[runbook](colab/README.md) for an isolated local stdio smoke test. The notebook
pins a specific `main` commit and explicitly adds its `src/` directory to
`sys.path` after editable installation; this prevents a common
`ModuleNotFoundError: No module named 'im19_mcp'` in an already-running
notebook kernel. It does **not** establish a live Google/ChatGPT connection.

## Safe deployment preflight

```bash
im19-mcp-doctor --mode local
im19-mcp-doctor --mode remote --check-database
```

The doctor prints a redacted JSON checklist. The remote mode validates required
authentication/encryption configuration and the Fernet key format. Database
readiness is tested **only** with `--check-database`, using the configured DB,
which may open a network connection. Passing the doctor is **not** production
acceptance and does not prove live Google or ChatGPT connectivity.

## Database and controlled staging

```bash
cp .env.example .env
# Set IM19_DATABASE_URL, IM19_FERNET_KEY, IM19_AUDIT_SALT,
# Google OAuth credentials and a separate MCP OAuth identity provider.
set -a; source .env; set +a
alembic upgrade head  # required revisions 0001–0005
im19-gateway
```

Use a secure environment/secrets manager rather than sourcing a production credentials file in a shared shell. Remote mode refuses to start without required MCP OAuth settings. Deploy behind a **trusted HTTPS proxy**, and verify issuer/resource audience, introspection, PKCE, callbacks and provider accounts in staging. The repository itself does not host a running service.

## Security / scope

- Provider token documents and PKCE verifiers are encrypted using Fernet.
- **Offline Fernet key rotation** supports temporary previous-key reads during isolated migration, transactional re-encryption of all credential/PKCE/pending-link records, and primary-only coverage checks. **Production gateways reject old-key fallbacks**; no online rotation or managed KMS is claimed.
- Access controls deny cross-user/cross-account access and missing provider scopes.
- The default MCP configuration **disables provider mutations**. An experimental, approval-gated `drive_create_folder` tool exists but is disabled without explicit operator opt-in. Phase 6 now contains a **database-backed one-time approval + idempotency reservation ledger**, with atomic audit events. **A trusted human approval portal and general write adapters remain unimplemented**; they must be independently authenticated before any future write execution.
- Production linking first authenticates the browser at the configured MCP issuer using a dedicated confidential client, Authorization Code + PKCE, resource-bound token introspection and exact subject/client matching. A secure HttpOnly SameSite cookie binds both callbacks. A ticket alone cannot authenticate a user.
- OAuth callback states are bound to a specific provider service, purpose and browser, atomically single-use, and expire after 10 minutes.
- Google callbacks store credentials in an encrypted pending vault, never as a connected account. The user must confirm a one-time code (5-minute TTL) from the browser via the authenticated MCP tool.
- Database-backed refresh leases and compare-and-set updates mitigate concurrent refresh, disconnect races and token resurrection.
- `im19-purge-expired` is a trusted maintenance command for expiring unused OAuth states, encrypted pending confirmations, governance approval grants **and pseudonymized rate windows**; schedule it in backend operations, not through MCP.
- Production gateway applies **PostgreSQL-backed shared quota admission** to ASGI socket peers (120/min), and limits verified MCP subjects before provider reads (60/min) or new Google OAuth consent (5/min). Failed quota DB access returns 503 / SERVICE_UNAVAILABLE; forwarding headers cannot choose a different quota identity. A trusted edge/WAF limiter is still required to distinguish end-user IPs behind reverse proxies.
- Production remote configuration defaults to fail-closed: PostgreSQL is mandatory, development subjects are forbidden, and OAuth callback configuration and audit salt are validated. Explicitly set `IM19_DEPLOYMENT_ENV=staging` only for controlled staging environments.
- `/health` indicates that the process is alive; `/ready` returns HTTP 503 unless database connectivity, Alembic revision `0005_distributed_quotas`, and every required table/column pass. Container readiness uses this endpoint.
- Stateless JSON HTTP transport supports worker switching without per-process MCP session affinity. Host/Origin checks cover all routes. Upstream reads have byte limits and total deadlines; JSON rejects duplicate keys and non-finite values. PostgreSQL connections, statements, locks and pool waits are bounded.
- Drive file content is never fetched with a metadata-only grant. **Both** `drive.metadata.readonly` and `drive.readonly` are Google restricted scopes; public use requires appropriate verification, with security assessment obligations depending on handling of the data.
- A real independent OAuth identity provider is required for ChatGPT-to-MCP authentication.
- CI uses mock provider responses, **not live Google API credentials**. A separate GitHub Actions job tests concurrent approval consumption, idempotency, **shared admission control across independent worker pools**, offline vault encryption key rotation, and a **synthetic encrypted backup/restore smoke test** on a disposable PostgreSQL 16 database. This is not a production disaster-recovery exercise.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Institutional governance and idempotency](docs/GOVERNANCE.md)
- [Offline credential encryption key rotation](docs/KEY_ROTATION.md)
- [Distributed gateway and verified-user quotas](docs/DISTRIBUTED_QUOTAS.md)
- [Google Drive connector and scope policy](docs/DRIVE_CONNECTOR.md)
- [Drive write prototype and acceptance limits](docs/DRIVE_WRITE.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Google OAuth setup](docs/OAUTH_SETUP.md)
- [ChatGPT integration](docs/CHATGPT_INTEGRATION.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Security checklist](docs/SECURITY_CHECKLIST.md)
- [Acceptance report](docs/ACCEPTANCE_REPORT.md)

Deploy only after completing the external acceptance gates in the [production readiness report](docs/PRODUCTION_READINESS.md). Production configuration refuses the legacy unbound Google flow. Existing credentials without browser proof require reauthorization. The mutation approval ledger remains internal. The prototype write tool is off by default; a human write-approval portal and general provider write adapters are outside this release.


## Opt-in browser inspection queue (staging only)

IM19 includes a **read-only**, owner-scoped browser inspection queue with
MCP submit/status/cancel tools and a separate Playwright worker. It is
disabled by default, does not provide Cloud Browser sessions or login,
and requires a dedicated egress-isolated worker before deployment. See
[Browser Queue v1](docs/BROWSER_QUEUE_V1.md).
