# IM19 deployment — controlled staging ONLY

## 1. Local prerequisites

Python 3.11+, official MCP SDK, current PostgreSQL, and a reverse proxy for
HTTPS. Staging uses a **separate** MCP OAuth/OIDC issuer and Google OAuth app.
GitHub Actions is CI, not a persistent hosting service.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
im19-mcp
```

With no verified OAuth principal (and no deliberate `IM19_DEV_SUBJECT`),
provider tools fail closed with `AUTH_REQUIRED`. Do NOT configure
`IM19_DEV_SUBJECT` for a publicly accessible MCP process.

## 2. Database schema and secrets

Provision a managed PostgreSQL database, application credentials with least
privilege, an independent random `IM19_FERNET_KEY`, and random
`IM19_AUDIT_SALT`. If the Fernet key must change, follow the [offline
key rotation runbook](KEY_ROTATION.md), including a verified backup, stopping
all writers, passing an explicit maintenance acknowledgment, and confirming
primary-only decryption before service restart. Never leave
`IM19_FERNET_PREVIOUS_KEYS` configured in a production gateway. Do not commit real `.env` or persist credentials in build
args, untrusted PR jobs or model-visible tool input.

```bash
# With a trusted staging DB URL in IM19_DATABASE_URL:
alembic upgrade head
# Expected current revision: 0004_governance
```

The migrations create state, pending-account confirmation, credential refresh
lease, one-time governance approval, and mutation idempotency receipt tables. **Existing databases must run all unapplied migrations
before upgrading the service**. `im19-init-db` is for local development only.

## 3. Docker Compose test environment

```bash
# Configure .env from .env.example with secure, independently provisioned values.
export POSTGRES_PASSWORD='set-a-unique-long-staging-secret'
docker compose build
docker compose up -d postgres
docker compose run --rm gateway alembic upgrade head
docker compose up -d gateway
```

Migration files and Alembic are included in the runtime Docker image. Do
not expose port 8000 to the public internet: it is loopback-bound on the host.
Put an HTTPS reverse proxy in front and explicitly configure trusted host,
Origin and query-string access log redaction. Use `/health` for process liveness;
use `/ready` to check live DB connectivity and Alembic revision
`0004_governance`. A 503 response must prevent instance registration.

Remote gateway config defaults to strict `production` validation even if
`IM19_DEPLOYMENT_ENV` is omitted. For development only, explicitly set
`IM19_DEPLOYMENT_ENV=staging` or `development`. Production requires
PostgreSQL, strong audit salt, no development subject, and a same-origin
HTTPS Google callback if the Google connector is configured.

The Google callback request contains OAuth `code` and `state` URL parameters.
Uvicorn HTTP access logging is disabled in the bundled entrypoint, but
**reverse proxies/CDNs/load balancers must also avoid logging query strings**
for `/oauth/google/callback`.

## 4. OAuth & scope acceptance checklist

1. Confirm independent MCP OAuth issuer, introspection, audience, expiry and
   scope rejection behavior (invalid tokens must fail closed).
2. Exercise `request_service_authorization` with a staging user.
3. Complete Google's consent in a staging browser. Callback MUST return
   `PENDING_CONFIRMATION`; no usable account may exist yet.
4. Copy the browser's 5-minute one-time code to the authenticated MCP tool
   `confirm_service_authorization`. A different user must not be able to
   redeem it; replay must fail.
5. Inspect `get_connection_status` and perform only supported read-only
   provider calls. Verify YouTube and Drive consent separately.
6. Trigger refresh concurrently across workers; verify the database lease
   prevents overwrite after disconnect or reauthorization.
7. Check `RATE_LIMITED` behavior, test restore procedures, scan egress paths,
   and review all Google's restricted-scope requirements.

## 5. OAuth retention operations

Schedule the following inside a trusted operations context after migrations:

```bash
im19-purge-expired
# Or with the container: docker compose run --rm gateway im19-purge-expired
```

It only returns deletion counts. Expired staged Google credentials, OAuth
states and unused governance approvals otherwise remain in the database after their logical TTL;
logical expiry alone is not a retention policy. A production scheduler, job
monitoring and operational alarm must be configured separately.

## 6. Release gates (NOT YET MET)

- Passing Python 3.11/3.12 CI, Docker build and dependency audit.
- First-party OIDC **authenticated browser** portal with session-bound link
  initiation and callback. The one-time code is only a safer interim fallback.
- Live identity-provider integration, Google test-account connections,
  end-to-end ChatGPT tool invocation, OAuth provider review where necessary.
- Independently authenticated human approval portal, approver role checks and
  future provider write-adapter reconciliation (read-only MCP stays enabled).
- Manual offline Fernet re-encryption is implemented; managed KMS/HSM custody,
  verified backup/restore drills and independent key rotation controls remain
  release gates.
- Managed monitoring and alerting, reliable
  audit/log retention and central distributed ingress rate limiting.
- Penetration test, incident response runbook and per-tenant access audit.

Use `python -m pytest -q` for unit/mock tests. In CI, the separate
`IM19 PostgreSQL Integration` workflow launches a disposable PostgreSQL 16
container, upgrades all migrations, and tests real concurrent approval
transactions without any production credentials. This is NOT a load test.

The CI job also performs a `pg_dump`/`pg_restore` round trip on a
**disposable PostgreSQL 16 database** and confirms that its migrated schema
is ready and one **synthetic encrypted OAuth credential** is decryptable
after restore. This is a limited smoke test only; it does **not** establish
production RPO/RTO, point-in-time recovery, offsite backup retention,
separated administrator permissions, disaster failover, or backup encryption
key escrow.

No production deployment, provider login, or ChatGPT connection is claimed.
