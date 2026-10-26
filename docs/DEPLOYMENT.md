# IM19 0.3.0 deployment runbook

This runbook targets the read-only gateway. Code validation and acceptance of a
particular deployment are separate; record both in PRODUCTION_READINESS.md.

## Provision the environment

Use Python 3.11/3.12 or the supplied Python 3.12 container, PostgreSQL 16, a
trusted HTTPS reverse proxy, and a production secret manager. Set a unique
Fernet key and independent random audit salt (at least 32 characters). Keep
IM19_DEV_SUBJECT and IM19_FERNET_PREVIOUS_KEYS empty in production.

Provision the MCP authorization server with:

- A canonical issuer, token introspection endpoint and resource audience equal
  to the exact IM19_MCP_RESOURCE, for example https://mcp.example.com/mcp.
- ChatGPT/client discovery and Authorization Code + PKCE support as required
  by that client; the gateway itself is a resource server, not an issuer.
- A server-side introspection client in IM19_MCP_CLIENT_ID/SECRET.
- A dedicated confidential browser client in IM19_BROWSER_CLIENT_ID/SECRET.
  Register exactly https://mcp.example.com/oauth/browser/callback. Configure
  its authorization and token endpoints. It must issue im19:read access tokens
  for the same resource and expose the same stable sub through introspection.
  Token exchange uses HTTP Basic client authentication and resource indicators.

For Google, register https://mcp.example.com/oauth/google/callback, configure
its separate Web application credentials and enable only the required APIs.
Production refuses Google linking without complete browser-client settings.
If Google is disabled, clear all Google and browser settings rather than
leaving partially configured credentials.

IM19_DATABASE_URL must contain the actual database host and a percent-encoded
password. POSTGRES_PASSWORD for the bundled database must represent that same
password. Compose does not concatenate an unescaped password into a URL.
Never print populated Compose configuration, environment dumps or secrets in
CI or support conversations.

## Build, migrate and start

```bash
# Secrets must already be available to Compose via the protected environment.
docker compose -f compose.yaml -f compose.production.yaml build
docker compose -f compose.yaml -f compose.production.yaml up -d postgres
docker compose -f compose.yaml -f compose.production.yaml run --rm gateway alembic upgrade head
docker compose -f compose.yaml -f compose.production.yaml run --rm gateway im19-mcp-doctor --mode remote --check-database
docker compose -f compose.yaml -f compose.production.yaml up -d gateway
```

The production override selects strict production validation. Gateway port
8000 is bound only to host loopback. The container runs as UID 10001 with a
read-only root filesystem, no capabilities and bounded resources/log rotation.
The runtime dependency file is version-pinned with package hashes. Recompile
it deliberately, run the full checks and review its diff before upgrading.

Use Alembic revision 0005_distributed_quotas. `im19-init-db` refuses non-SQLite
bootstrap; it cannot replace migrations on PostgreSQL. Readiness checks actual
tables/columns as well as the revision, and returns 503 on drift or outage.
Health only establishes process liveness. Configure the proxy/load balancer
and monitoring to respect readiness; Docker health alone does not restart an
unhealthy container automatically.

## Configure ingress and operations

Forward the canonical Host, restrict browser origins to the public HTTPS
origin, terminate TLS, and apply real-client rate limits at the trusted edge.
Do not trust arbitrary X-Forwarded-For. Gateway quotas see the immediate socket
peer and can combine users behind the same proxy; size ingress and deployment
capacity accordingly. The HTTP protocol is stateless, so MCP requests may
switch between workers sharing the same DB and secret configuration.

Do not log URL queries on /connect/google, /oauth/browser/callback or
/oauth/google/callback. The bundled entrypoints disable HTTP access logs and
HTTPX/httpcore request logs. Apply equivalent redaction at every ingress,
trace collector and error reporting service. Monitor 401/403/429/503 rates,
readiness failures, fixed sanitized backend/audit errors, database pool waits,
quota latency, storage growth and cleanup failures. Define incident owners.

Schedule `im19-purge-expired` at least every five minutes in a trusted
operations context, with the same DB/key/audit configuration. For a container
host, deploy the supplied systemd service/timer after setting WorkingDirectory
to the checkout and protecting its environment. Install the units in /etc/systemd/system,
run systemctl daemon-reload, then systemctl enable --now im19-retention.timer,
and verify systemctl status im19-retention.timer. Configure alerting on service
failures. Never expose cleanup as MCP.
Define separate audit and mutation-evidence retention before production use;
cleanup intentionally does not erase permanent audit/receipt history.

Use managed PostgreSQL backups/PITR and segregated key custody. Perform a real
restore drill with the deployment's keys and record RPO/RTO. Follow KEY_ROTATION.md
for offline rewrapping; verify primary-only coverage before restarting writers.

## Acceptance and rollback

Before admitting real users, complete the live tests in PRODUCTION_READINESS.md:
issuer rejects wrong audiences/expired/future tokens, browser subject mismatch
and replay fail, Google/ChatGPT supported read tools work, and outages/quotas
fail closed. Use test accounts and record timestamps and redacted evidence.
Existing 0.2 credentials and pending grants without browser proof require
reauthorization; production reads cannot reuse them.

Rollback by stopping gateway traffic and restoring the prior tested image,
configuration and compatible DB backup if needed. This release adds no schema
migration, but browser-flow JSON purpose bindings and browser-proof credentials
are a security boundary: do not roll back to an unbound public linking flow.
Check schema readiness and run smoke tests before reopening traffic. Provider
writes remain unavailable in this release.
