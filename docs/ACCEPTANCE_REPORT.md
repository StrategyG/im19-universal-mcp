# IM19 — Acceptance Test Report (Phase 1–3)

Last evaluated: 2026-10-24 UTC. **This is a test environment report, not
production acceptance.** Evidence is linked to exact GitHub Actions runs.

## Automated tests verified

- Python 3.11: CI success.
- Python 3.12: CI success; **35 tests passed**.
- Docker image: successfully built.
- Ruff and mypy jobs: completed successfully.
- Security workflow (Ruff, Bandit, pip-audit): successful, pip-audit:
  **"No known vulnerabilities found"** for audited installed dependencies.
- Explicit dependency remediation: cryptography minimum upgraded to 50.0.2
  after security workflow identified known issues in 47.0.0.

Evidence:
- [CI run 40120592202](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40120592202)
- [Security run 40120592236](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40120592236)
- Verified revision: `85aa77d944a68a192f6c588579018372f3bd0617`.

## Coverage by evidence type

| Criterion | Mode | Result |
|---|---|---|
| MCP tool/resource/prompt discovery | local SDK integration | PASS |
| MCP stdio startup | local SDK subprocess | PASS |
| Remote token issuer, audience, scope, expiry | mocked introspection | PASS |
| OAuth URL with PKCE and incremental scopes | unit test | PASS |
| OAuth callback state one-time, expiration | SQLite+unit test | PASS |
| OAuth code exchange and refresh | mocked Google endpoint | PASS |
| Encrypted credential storage / user isolation | SQLite+Fernet | PASS |
| Read-only YouTube channel/uploads/Analytics | mocked official API paths | PASS |
| Provider rate-limit normalization and invalid inputs | mocked HTTP | PASS |
| Gateway per-process throttling | ASGI integration | PASS |
| Docker build | CI image build | PASS |
| Actual Google OAuth account login | live | **NOT TESTED** |
| Actual YouTube Data/Analytics | live | **NOT TESTED** |
| ChatGPT remote MCP OAuth login | live | **NOT TESTED** |
| External social connectors | implementation | **NOT IMPLEMENTED** |
| Durable approval / mutations | implementation | **NOT IMPLEMENTED** |

## Unresolved production gates

Requires real provider test accounts, a separate compliant MCP OAuth identity
provider, public HTTPS deployment, distributed rate limiting, token rotation,
staging pen test, disconnect UX, write-approval persistence and recovery tests.

Do not label this system production-ready based on passing mock tests.
