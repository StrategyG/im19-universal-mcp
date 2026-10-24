# IM19 — Acceptance Test Report (Phases 1–4, 6–7, offline key lifecycle)

Last evaluated: 2026-10-24 UTC. **This is a test environment report, not
production acceptance.** Evidence is linked to exact GitHub Actions runs.

## Automated tests verified

- Python 3.11: CI success.
- Python 3.12: CI success; **124 tests passed, 5 PostgreSQL-only tests skipped** on commit `32e9e88d` (shared quotas and provider-scope isolation).
- PostgreSQL 16 service CI: **5 integration tests passed**, including concurrent approval/idempotency, full sensitive-table Fernet key rotation, shared quota admission across four worker pools and namespace isolation.
- Synthetic encrypted PostgreSQL 16 `pg_dump`/`pg_restore`: **PASS**, restored schema revision and a job-only credential decrypt verified. This is not a production DR drill.
- Production configuration fail-closed tests, database readiness, audit and approval replay tests included.
- Docker image: successfully built.
- Ruff and mypy jobs: completed successfully.
- Security workflow (Ruff, Bandit, pip-audit): successful, pip-audit:
  **"No known vulnerabilities found"** for audited installed dependencies.
- Explicit dependency remediation: cryptography minimum upgraded to 50.0.2
  after security workflow identified known issues in 47.0.0.

Evidence:
- [CI run 40129594889](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40129594889)
- [Security run 40129594877](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40129594877)
- [PostgreSQL integration run 40129594906](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40129594906)
- Verified revision: `32e9e88d8bb5a5db2d00d4132bf31d7baaba1577`.
  This acceptance report is a later documentation-only revision. Review the latest PR checks before merge.

## Coverage by evidence type

| Criterion | Mode | Result |
|---|---|---|
| MCP tool/resource/prompt discovery | local SDK integration | PASS |
| MCP stdio startup | local SDK subprocess | PASS |
| Remote token issuer, audience, scope, expiry | mocked introspection | PASS |
| OAuth URL with PKCE and incremental scopes | unit test | PASS |
| OAuth callback state one-time, expiration and service binding | SQLite+unit test | PASS |
| OAuth code exchange and refresh | mocked Google endpoint | PASS |
| Callback stages encrypted credentials, never connects directly | mocked Google + SQLite tests | PASS |
| Verified MCP confirmation, wrong-user and replay denial | MCP client + SQLite tests | PASS |
| Concurrent refresh lease, stale-worker CAS and disconnect | SQLite tests | PASS |
| Alembic migrations 0001–0005 | SQLite migration integration + PostgreSQL upgrade | PASS |
| Callback anti-framing, no-store headers and duplicate state rejection | ASGI integration mock | PASS |
| Local stored-credential status makes no false live claim | in-process MCP mock | PASS |
| Expired OAuth state / staged token retention purge | SQLite unit test | PASS |
| Encrypted credential storage / user isolation | SQLite+Fernet | PASS |
| Offline all-sensitive-field Fernet rotation / primary-only verification | SQLite and PostgreSQL integration | PASS |
| Failed rotation rollback / key-retirement and log redaction tests | SQLite / CLI tests | PASS |
| Production runtime denies legacy Fernet key fallback | config regression tests | PASS |
| PostgreSQL backup/restore of synthetic encrypted data and schema | disposable GitHub Actions service | PASS (LIMITED SMOKE TEST) |
| Read-only YouTube channel/uploads/Analytics | mocked official API paths | PASS |
| Drive metadata search, folder list, metadata | mocked Google Drive API v3 | PASS |
| Drive opt-in text access, MIME/size caps | mocked Drive API, scope separation | PASS |
| Drive vs YouTube identity and OAuth isolation | in-process MCP client and vault | PASS |
| Provider rate-limit normalization and invalid inputs | mocked HTTP | PASS |
| Gateway per-process throttling (local/staging) | ASGI integration | PASS |
| Production PostgreSQL shared HTTP peer quota, 429/503 fail-closed | ASGI + SQLite tests | PASS |
| MCP principal read/OAuth quotas, verified subject only | MCP integration + SQLite | PASS |
| Atomic shared limits across four PostgreSQL worker pools | Real PostgreSQL 16 concurrency tests | PASS |
| Quota keys do not store raw IP/subject; X-Forwarded-For ignored | SQL row inspection + ASGI tests | PASS |
| Quota cleanup and database outage denial | SQLite tests | PASS |
| Approval binding (subject, provider, account, resource and SHA-256 payload) | SQLite unit tests | PASS |
| One-use, expiring approvals + exact idempotency receipt | SQLite unit + PostgreSQL concurrent transactions | PASS |
| Unknown external write outcome cannot auto-retry | Durable RESERVED receipt / policy | IMPLEMENTED AS PRIMITIVE, LIVE NOT TESTED |
| Production remote startup fails on SQLite, weak salt, dev user | configuration unit tests | PASS |
| Database health vs readiness endpoint | HTTP/ASGI + Alembic-version unit tests | PASS |
| Docker build | CI image build | PASS |
| Actual Google OAuth account login | live | **NOT TESTED** |
| Actual YouTube Data/Analytics | live | **NOT TESTED** |
| Actual Google Drive API | live | **NOT TESTED** |
| Google Drive restricted-scope approval | compliance | **NOT VERIFIED** |
| ChatGPT remote MCP OAuth login | live | **NOT TESTED** |
| External social connectors | implementation | **NOT IMPLEMENTED** |
| Durable approval and idempotency **storage primitive** | SQLite + PostgreSQL tests | PASS |
| Independently authenticated human approval portal | implementation | **NOT IMPLEMENTED** |
| Actual provider mutation + recovery reconciliation | live | **NOT IMPLEMENTED** |

## Unresolved production gates

Requires real provider test accounts, a separate compliant MCP OAuth identity
provider, public HTTPS deployment, trusted HTTPS ingress/WAF per-client-IP limits,
quota overload and clock-skew tests, **managed KMS/HSM
custody**, operator-verified offline rotation, real production backup/restore
and RPO/RTO drills, staging pen test, restricted Drive scope consent/compliance, independently
verified browser-session binding, disconnect UX, authenticated human approval issuance,
external write-provider reconciliation and recovery tests. One-time confirmation code mitigates silent linking but is
not equivalent to first-party login session binding.

Do not label this system production-ready based on passing mock tests.
