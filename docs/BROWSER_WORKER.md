# IM19 opt-in browser worker — foundation only

This change introduces an **experimental, local, read-only Chromium inspection
worker**. It is **not** registered as an MCP tool, not wired to existing OAuth
sessions, and not advertised as an application controller or production-ready
automation. No local model or GPU is necessary.

## Installation

The core MCP installation remains unchanged. In an isolated development
environment install the optional browser extra and the supported Chromium build:

```bash
python -m pip install -e '.[browser]'
python -m playwright install chromium
```

Use an isolated non-root browser worker rather than installing Chromium inside
the production gateway. Browser binaries and system dependencies vary by OS and
architecture. Verify ARM64 compatibility independently; a Docker build alone
does not prove browser execution on Armbian.

## Explicit local inspection

```bash
python -m im19_mcp.browser_worker \
  https://example.com/ \
  --allow-origin https://example.com
```

Only exact, listed HTTPS origins are allowed. Redirects and subresources outside
the allowlist are aborted; websockets are denied, and service workers are
disabled. Every request uses an ephemeral browser context: no persistent login
cookies, downloaded files, uploads, credentials, or exposed page HTML.

Successful output confirms only browser inspection for that page. It does not
confirm MCP client connectivity, Google login, or provider authorization.

## Security boundaries

- The Python policy is **not** a network sandbox. Deploy behind an enforced
  outbound network policy that blocks RFC1918/loopback/link-local/cloud metadata
  and performs DNS-resolution enforcement against rebinding. Untrusted browser
  pages can otherwise reach internal addresses through DNS changes.
- Restrict CPU, memory, process count, filesystem, and container capabilities.
- Never mount the gateway's provider tokens or its credential vault into a
  browser worker. Do not expose a browser debugging port publicly.
- Browser sessions must be identity-bound and subject to per-operation approval
  before any future MCP-facing controls or writes are implemented.
- Browser automation must not bypass CAPTCHA, MFA, or provider restrictions.
- The optional dependency is separate so gateway CI/install does not require a
  browser or GPU; use a separately locked artifact before production use.

## Verification and limitations

Unit tests use a synthetic browser engine to check blocked URLs, disallowed
redirects, blocked websockets, isolated context options, and browser teardown.
They do **not** prove that Chromium launched in Codespaces/Armbian, that
network-level egress controls exist, or that app logins work.

A future separately reviewed PR must provide: hardened dedicated browser
container, real Playwright integration tests, per-user session identity,
approval and audit integration, browser-to-MCP tool registration, and deployment
validation on real ARM64 hardware.
