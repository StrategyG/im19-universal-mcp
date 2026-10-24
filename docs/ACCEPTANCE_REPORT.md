# IM19 — Acceptance Test Report (Phase 1–4)

Last evaluated: 2026-10-24 UTC. **This is a test environment report, not
production acceptance.** Evidence is linked to exact GitHub Actions runs.

## Automated tests verified

- Python 3.11: CI success.
- Python 3.12: CI success; **55 tests passed** on commit `1216eb0` (Drive MCP integration).
- Docker image: successfully built.
- Ruff and mypy jobs: completed successfully.
- Security workflow (Ruff, Bandit, pip-audit): successful, pip-audit:
  **"No known vulnerabilities found"** for audited installed dependencies.
- Explicit dependency remediation: cryptography minimum upgraded to 50.0.2
  after security workflow identified known issues in 47.0.0.

Evidence:
- [CI run 40121827610](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40121827610)
- [Security run 40121827625](https://github.com/StrategyG/im19-universal-mcp/actions/runs/40121827625)
- Verified revision: `1216eb0ca9d6658498bd5ebefd4ce994cf011897`.
  Documentation-only commits after this revision require their own CI checks.

## Coverage by evidence type

| Criterion | Mode | Result |
|---|---|---|
| MCP tool/resource/prompt discovery | local SDK integration | PASS |
| MCP stdio startup | local SDK subprocess | PASS |
| Remote token issuer, audience, scope, expiry | mocked introspection | PASS |
| OAuth URL with PKCE and incremental scopes | unit test | PASS |
| OAuth callback state one-time, expiration and service binding | SQLite+unit test | PASS |
| OAuth code exchange and refresh | mocked Google endpoint | PASS |
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
provider, public HTTPS deployment, distributed rate limiting, token rotation,
staging pen test, restricted Drive scope consent/compliance, browser session
binding, disconnect UX, write-approval persistence and recovery tests.

Do not label this system production-ready based on passing mock tests.
