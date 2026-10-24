# IM19 Universal MCP — Intelligent Mind 19

**Status:** development implementation of Phase 1 (MCP core), Phase 2 (Google OAuth backend), Phase 3 (YouTube read-only), Phase 4 (Google Drive read-only), and Phase 6 governance *storage primitives*. This project is **not deployed** and no live Google, YouTube or ChatGPT connection has been verified.

## Architecture

```text
ChatGPT (remote OAuth MCP client)
   -> IM19 authenticated Streamable HTTP gateway
      -> MCP tool registry and server-side subject
      -> encrypted OAuth vault (PostgreSQL/SQLAlchemy)
      -> Google OAuth Authorization Code + PKCE
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

Other providers are registered as **not implemented**, not falsely advertised as connected.

## Development run

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m compileall -q src tests
im19-mcp
```

Local `stdio` mode requires no Google credentials for tool discovery. If `IM19_DEV_SUBJECT` is set, it represents a **trusted local-development identity only**; remote mode never uses it.

## Database and controlled staging

```bash
cp .env.example .env
# Set IM19_DATABASE_URL, IM19_FERNET_KEY, IM19_AUDIT_SALT,
# Google OAuth credentials and a separate MCP OAuth identity provider.
set -a; source .env; set +a
alembic upgrade head  # required revisions 0001–0004
im19-gateway
```

Use a secure environment/secrets manager rather than sourcing a production credentials file in a shared shell. Remote mode refuses to start without required MCP OAuth settings. Deploy behind a **trusted HTTPS proxy**, and verify issuer/resource audience, introspection, PKCE, callbacks and provider accounts in staging. The repository itself does not host a running service.

## Security / scope

- Provider token documents and PKCE verifiers are encrypted using Fernet.
- **Offline Fernet key rotation** supports temporary previous-key reads during isolated migration, transactional re-encryption of all credential/PKCE/pending-link records, and primary-only coverage checks. **Production gateways reject old-key fallbacks**; no online rotation or managed KMS is claimed.
- Access controls deny cross-user/cross-account access and missing provider scopes.
- The MCP service exposes **no provider mutation tools**. Phase 6 now contains a **database-backed one-time approval + idempotency reservation ledger**, with atomic audit events. **Trusted human portal and actual write adapters remain unimplemented**; they must be independently authenticated before any future write execution.
- OAuth callback states are bound to a specific provider service, atomically single-use, and expire after 10 minutes.
- Google callbacks store credentials in an encrypted pending vault, never as a connected account. The user must confirm a one-time code (5-minute TTL) from the browser via the authenticated MCP tool.
- Database-backed refresh leases and compare-and-set updates mitigate concurrent refresh, disconnect races and token resurrection.
- `im19-purge-expired` is a trusted maintenance command for expiring unused OAuth states, encrypted pending confirmations and governance approval grants; schedule it in backend operations, not through MCP.
- Production remote configuration defaults to fail-closed: PostgreSQL is mandatory, development subjects are forbidden, and OAuth callback configuration and audit salt are validated. Explicitly set `IM19_DEPLOYMENT_ENV=staging` only for controlled staging environments.
- `/health` indicates that the process is alive; `/ready` returns HTTP 503 unless database connectivity and Alembic revision `0004_governance` pass.
- Drive file content is never fetched with a metadata-only grant. **Both** `drive.metadata.readonly` and `drive.readonly` are Google restricted scopes; public use requires appropriate verification, with security assessment obligations depending on handling of the data.
- A real independent OAuth identity provider is required for ChatGPT-to-MCP authentication.
- CI uses mock provider responses, **not live Google API credentials**. A separate GitHub Actions job additionally tests concurrent approval consumption and idempotency on a disposable PostgreSQL 16 instance.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Institutional governance and idempotency](docs/GOVERNANCE.md)
- [Offline credential encryption key rotation](docs/KEY_ROTATION.md)
- [Google Drive connector and scope policy](docs/DRIVE_CONNECTOR.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Google OAuth setup](docs/OAUTH_SETUP.md)
- [ChatGPT integration](docs/CHATGPT_INTEGRATION.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Security checklist](docs/SECURITY_CHECKLIST.md)
- [Acceptance report](docs/ACCEPTANCE_REPORT.md)

**Do not deploy as production-ready** before completing the unchecked security controls and successfully running live integration and acceptance tests. The current confirmation-code flow is a safer development fallback, **not a substitute for authenticated browser-session binding in production**.
