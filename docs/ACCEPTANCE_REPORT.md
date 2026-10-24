# IM19 — Acceptance Test Report (Phase 1–4)

Last evaluated: 2026-10-24 UTC. **This is a test environment report, not
production acceptance.** Evidence is linked to exact GitHub Actions runs.

## Automated tests verified

- Python 3.11: CI success.
- Python 3.12: CI success; **68 tests passed** on commit `3b600c12` (OAuth confirmation, refresh locks, retention cleanup and callback hardening).
- Docker image: successfully built.
- Ruff and mypy jobs: completed successfully.
- Security workflow (Ruff, Bandit, pip-audit): successful, pip-audit:
  **"No known vulnerabilities found"** for audited installed dependencies.
- Explicit dependency remediation: cryptography minimum upgraded to 50.0.2
  after security workflow identified known issues in 47.0.0.

Evidence:
- [CI run 40123602439](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40123602439)
- [Security run 40123602436](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40123602436)
- Verified revision: `3b600c1228c44c06e557f75ec5a8432885eb4646`.
  Later documentation-only commits must be checked independently.

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
| Alembic migrations 0001–0003 | SQLite migration integration | PASS |
| Callback anti-framing, no-store headers and duplicate state rejection | ASGI integration mock | PASS |
| Local stored-credential status makes no false live claim | in-process MCP mock | PASS |
| Expired OAuth state / staged token retention purge | SQLite unit test | PASS |
| Encrypted credential storage / user isolation | SQLite+Fernet | PASS |
| Read-only YouTube channel/uploads/Analytics | mocked official API paths | PASS |
| Drive metadata search, folder list, metadata | mocked Google Drive API v3 | PASS |
| Drive opt-in text access, MIME/size caps | mocked Drive API, scope separation | PASS |
| Drive vs YouTube identity and OAuth isolation | in-process MCP client and vault | PASS |
| Provider rate-limit normalization and invalid inputs | mocked HTTP | PASS |
| Gateway per-process throttling | ASGI integration | PASS |
| Docker build | CI image build | PASS |
| Actual Google OAuth account login | live | **NOT TESTED** |
| Actual YouTube Data/Analytics | live | **NOT TESTED** |
| Actual Google Drive API | live | **NOT TESTED** |
| Google Drive restricted-scope approval | compliance | **NOT VERIFIED** |
| ChatGPT remote MCP OAuth login | live | **NOT TESTED** |
| External social connectors | implementation | **NOT IMPLEMENTED** |
| Durable approval / mutations | implementation | **NOT IMPLEMENTED** |

## Unresolved production gates

Requires real provider test accounts, a separate compliant MCP OAuth identity
provider, public HTTPS deployment, central ingress rate limiting, token rotation,
staging pen test, restricted Drive scope consent/compliance, independently
verified browser-session binding, disconnect UX, write-approval persistence
and recovery tests. One-time confirmation code mitigates silent linking but is
not equivalent to first-party login session binding.

Do not label this system production-ready based on passing mock tests.
