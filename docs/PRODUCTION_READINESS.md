# IM19 0.3.0 — production readiness evidence

Assessment date: 26 October 2026 (Asia/Jakarta). Baseline: main commit
409632e081587cfaa635402bc09abb58c989c422. Scope: authenticated, read-only
YouTube/Analytics and Drive metadata/text gateway. No deployment or live
provider connection is implied by repository changes.

## Corrected release blockers

- Production account linking now requires a dedicated browser Authorization
  Code + PKCE flow at the configured MCP issuer. Introspection must prove the
  exact requesting subject, dedicated client, resource, issuer, scope and
  valid token times. Google consent starts only after this proof.
- Both callbacks use a Secure HttpOnly SameSite cookie plus purpose-bound,
  expiring single-use state. Production rejects unbound callbacks, legacy
  pending grants and credentials without browser proof. Reauthorization is
  required for legacy credentials.
- Account activation, pending-code consumption and success audit commit in
  one transaction. Conditional UPSERT prevents a competing initial account
  from rebinding the user's existing connection. Audit failure rolls back
  activation and preserves a valid claim for retry.
- Token validation rejects non-finite/invalid times, future nbf and oversized
  identities; expiry reaches the SDK. Governance approvals also reject invalid
  expiration values, and canonical payloads reject nested non-string keys.
- Upstream HTTP reads are bounded by decoded byte count and total deadline.
  Redirects, duplicate JSON keys and non-standard numbers fail closed.
  Drive int64 string sizes are checked before content download.
- Stateless JSON HTTP permits worker switching with fresh request identity.
  Host/Origin checks cover every route, including browser callbacks. Secrets
  are removed from Settings repr, request logging is suppressed at the bundled
  entrypoints, and public errors omit backend exception messages.
- PostgreSQL connection, query, lock and pool waits are bounded. Async provider
  paths move database work off the event loop. Worker shutdown disposes pools.
  Readiness validates all required tables/columns, not only a revision marker.
- Docker installs hash-pinned runtime dependencies, probes readiness and runs
  without root/capabilities. A strict production Compose override, cleanup timer,
  deployment/rollback instructions and clean-wheel CI checks are provided.

**Code verification: PASS. Deployment acceptance: pending actual environment.**

## Executed checks

| Check | Evidence/status |
|---|---|
| Baseline Python 3.12 suite | PASS: 138 passed; 6 PostgreSQL cases skipped locally |
| Updated Python 3.12 suite | PASS: 186 passed; 7 PostgreSQL cases reserved for CI |
| Python 3.11 clean wheel environment | PASS: 186 passed, 7 PostgreSQL cases skipped locally; hash-verified install and local doctor also pass |
| MCP stdio subprocess and in-process discovery | PASS in unit suite; no provider credentials |
| HTTP protocol and worker switch | PASS: real ASGI MCP requests across independent apps, separate subjects, invalid bearer rejection |
| Browser linking | PASS with mocked issuer/Google and real encrypted state; wrong subject/client/cookie and replay denied |
| Ruff, Bandit, policy mypy, compileall, pip check | PASS locally |
| pip-audit | No known installed-dependency vulnerabilities; local project itself is not a PyPI vulnerability entry |
| Distribution build | PASS: sdist and wheel 0.3.0 |
| Docker build | PASS in [IM19 CI](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40268354199) |
| PostgreSQL 16 transactions/restore | PASS: 7 integration cases, migrations and encrypted synthetic backup/restore in [PostgreSQL CI](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40268354062) |

GitHub Actions passed for implementation commit
43a4a4e23de349cad7364f3944b891875e57d29d: [Python 3.11/3.12, distribution,
clean-wheel installation and Docker](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40268354199),
[security/dependency audit](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40268354080),
and [PostgreSQL migrations, encrypted backup/restore and concurrency](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40268354062).
The local executor could not start PostgreSQL under its UID mapping; actual
PostgreSQL evidence comes from the disposable CI service. These checks do not
establish production backup recovery, load capacity or live OAuth.

## Deployment acceptance still required

| Gate | Required acceptance evidence |
|---|---|
| HTTPS and ingress | Valid certificate/canonical host, query-log redaction, real-client quotas, blocked foreign Origin/Host and tested 429/503 behavior |
| Identity provider | Registered browser client, exact callback, HTTP Basic token authentication, PKCE/resource support, introspection sub/client matching; invalid/future/expired token rejection |
| Google and ChatGPT | Real test-account consent, same-subject confirmation, supported read invocation and refresh; denied/replayed/wrong-user flow checks |
| Google scope approval | Applicable public-use and restricted-scope verification completed for the chosen scopes |
| Database and keys | Least-privilege runtime role, managed secret custody, monitored backups/PITR, real restore/key-recovery drill with agreed RPO/RTO |
| Operations | Enabled monitored cleanup timer, audit retention, incident owner/rollback, representative load and tenant-isolation/security review |

These gates need the actual deployment and test accounts. No issuer, Google
project, production server or ChatGPT connection was provisioned in this task.
The code is a release candidate for review and staging acceptance; it must not
be labeled a verified live production deployment until those gates pass.

Provider write adapters and a human write-approval UI remain out of scope for
this read-only release. The internal approval/idempotency primitives do not
advertise or authorize provider mutation tools.
