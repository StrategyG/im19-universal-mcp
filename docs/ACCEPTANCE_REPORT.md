# Acceptance Report — phase 1–3

Evidence levels are separated: STATIC, MOCK, SANDBOX and LIVE.

| Criterion | Evidence planned | Status |
|---|---|---|
| Import/compile | Python CI matrix | Pending GitHub Actions |
| MCP tool discovery | Official MCP Python client, in-process and stdio | Pending GitHub Actions |
| State generation/replay denial | Unit tests with SQLite | Pending GitHub Actions |
| Token encryption/cross-user isolation | SQLite + Fernet tests | Pending GitHub Actions |
| OAuth exchange and refresh | httpx.MockTransport | MOCK only; pending CI |
| YouTube Data/Analytics | httpx.MockTransport | MOCK only; pending CI |
| Remote MCP OAuth | Live issuer and introspection | NOT TESTED |
| Google OAuth consent | Google test account | NOT TESTED |
| YouTube API data | Google test account | NOT TESTED |
| ChatGPT connection | Workspace HTTPS setup | NOT TESTED |
| Human approval and mutation | Not exposed in this phase | NOT IMPLEMENTED |

Do not mark acceptance passed merely because code or mock tests exist.
See GitHub Actions for authoritative per-commit test results.
