# IM19 Browser Inspection Queue v1 — staging, read-only

**Scope: code implementation only.** This extension adds authenticated,
owner-scoped MCP tools and a database-backed job queue for ephemeral, read-only
Chromium page title inspection. This is **not ChatGPT Work Cloud Browser**,
not an interactive session, and not a general browser agent. No provider login,
click, type, screenshot, upload, download, browser profile or credential
transfer is supported.

## MCP contract

| Tool | Effect |
|---|---|
| `browser_submit_inspection(url, idempotency_key)` | Enqueue an exactly allowlisted HTTPS page; requires verified MCP subject |
| `browser_inspection_status(job_id)` | Subject-scoped status, sanitized origin/title and blocked request count |
| `browser_cancel_inspection(job_id)` | Subject-scoped cancel; running inspection may still finish but cannot publish result |
| `im19://browser/inspection-v1` | Capability declaration; **never** asserts a live worker is connected |

No MCP action runs Chromium inside the gateway. A separate worker polls jobs
with PostgreSQL `FOR UPDATE SKIP LOCKED`, crash-expiring leases, a maximum
of three attempts and no auth tokens in MCP responses. Submission is
idempotent with a caller-selected 12–64-character opaque key. Job requests
are constrained to a strict exact HTTPS-origin allowlist and reject queries,
fragments and URL credentials so tokens are not stored in browser job URLs.

## Staging configuration

Gateway configuration (production setting defaults to DISABLED):

```ini
IM19_ENABLE_BROWSER_JOBS=false
IM19_BROWSER_INSPECTION_ORIGINS=https://example.com
```

To stage only after database migration and separate worker hardening:
```bash
# Approved schema upgrade using a separate migration DB principal:
alembic upgrade head
# The deployed app and worker must use the same PostgreSQL schema.
IM19_ENABLE_BROWSER_JOBS=true
IM19_BROWSER_INSPECTION_ORIGINS=https://example.com
```

The browser worker is **NOT** started with the gateway's runtime environment.
Run it with a dedicated operating-system identity and a dedicated DB user
that can SELECT/INSERT/UPDATE only `browser_inspection_jobs`. The private
worker must not receive IM19_FERNET_KEY, provider OAuth tokens, browser account
cookies, gateway browser OAuth credentials, or direct public inbound ports.
The worker constructs an unrelated ephemeral Fernet instance solely because
the existing Vault helper manages SQLAlchemy sessions. This key is not used
to decrypt any shared data.

The worker's protected environment needs only the isolated database URL,
`IM19_ENABLE_BROWSER_JOBS=true`,
`IM19_BROWSER_INSPECTION_ORIGINS=https://example.com`, and
`IM19_BROWSER_WORKER_ACK=YES`:

```bash
# Only on an isolated, non-root worker host/service:
python -m pip install -e '.[browser]'
python -m playwright install chromium
im19-browser-queue-worker --loop --poll-seconds 3
```

The CLI is for a process supervisor (systemd/Kubernetes), not for exposure
through MCP and not a promise of continuous operation. Verify Chromium can
launch with its sandbox enabled and that the host has required libraries.

## Mandatory network/isolation release gate

**CRITICAL: Python origin validation is not an SSRF sandbox.** Before any
non-synthetic navigation or public deployment, apply independent egress
firewall/network namespace rules that reject loopback, RFC1918, link-local,
ULA, IPv4/IPv6 private/metadata service ranges, plus DNS rebinding protection.
The browser's proxy/DNS routing must honor these rules after redirects and on
subresource fetches, not merely during preflight. Deny direct access to cloud
metadata endpoints, Docker socket, gateway DB network and internal services.
Enforce no privileged container, no mounted host browser profile, no shared
credentials, read-only filesystem, CPU/memory/process limits, sandbox-enabled
Chromium and outbound HTTPS via an explicit allowlist proxy.

Do **not** enable this feature in a production gateway before:
1. A live Chromium test on a dedicated worker verifies real HTTPS pages,
   denied redirects, DNS/IP egress, resource limits and browser teardown.
2. Multi-tenant authenticated MCP tests use a real identity provider.
3. A PostgreSQL integration run exercises real cross-worker lease contention,
   retry, cancellation and recovery.
4. Dedicated database-role grants, monitoring and retention cleanup are
   implemented and tested. Completed job records currently require a manually
   managed retention policy; do not use this with sensitive URLs.
5. Security assessment explicitly approves remote website inspection and
   acceptable page types. Pages may contain untrusted instructions; their
   titles must be treated as untrusted data.

## Rollout dependencies

This branch is stacked atop PR #21 gateway production hardening and introduces
Alembic revision `0007_browser_inspection_jobs` after
`0006_write_approvals`. Draft PR #19 (ARUSIA) independently introduces
revision `0007_arusia_workflow`. **Do not merge both migration histories
without reordering/rebasing and validating a single Alembic head.**

Unit tests and mocked MCP tests prove queue behavior only. CI does not prove
internet egress controls, actual ARM64 Chromium, credential isolation,
production load or live Cloud Browser availability.

### Rollback

Disable `IM19_ENABLE_BROWSER_JOBS` at the gateway before rollback.
Drain/cancel jobs and shut down workers, preserve database backup, then
reconcile migration heads. Do not drop the browser task table while an
authenticated worker is still polling it.
