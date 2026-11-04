# IM19 production go/no-go gates — 28 October 2026

**Verdict: NOT PRODUCTION READY until real deployment evidence exists.**

This document is a deployment acceptance procedure, not a certificate.
A passing CI build or a successful mocked OAuth test cannot establish that
ChatGPT, Google, a public HTTPS endpoint or disaster recovery work in production.

## Release scope and baseline

The current `main` exposes the authenticated IM19 MCP gateway and optional
Drive write approval. ARUSIA integration and bounded autonomy remain separately
reviewable draft PRs #19 and #20. They must not be treated as deployed services.

For the initial release, keep `IM19_ENABLE_APPROVED_DRIVE_WRITES=false`.
Enable Google scopes and provider mutations only after separate privacy,
consent and security acceptance.

## Blocking gates (all required for GO)

| Gate | Operator evidence (must be redacted and time-stamped) |
|---|---|
| GitHub source | Exact deployed 40-character SHA and 4 successful CI workflows on that SHA |
| Protected source | Main branch ruleset preventing direct pushes, requiring review and checks; verify against GitHub API |
| Build integrity | Approved container image digest, hash-pinned dependencies, no secrets in image |
| Identity | Live OAuth discovery and introspection for correct issuer/audience/scope, expired/wrong-subject rejection |
| Browser account linking | Real short-lived PKCE sign-in, same verified subject, wrong-user/CSRF/replay negative tests |
| MCP client | ChatGPT test account lists tools and completes an authenticated read-only MCP call |
| Google | Separate Google test account consent and verified read call, no unapproved scopes |
| Ingress | Trusted HTTPS certificate, Host/Origin rejection, request/body size limits, WAF rate limits, query redaction |
| Database | Managed PostgreSQL, least-privilege runtime role, reviewed Alembic migration with backup/rollback |
| Recovery | Redacted encrypted backup + real restore drill with key recovery; documented RPO/RTO |
| Secrets | Managed key custody, documented rotation/revocation, no credentials in Git or logs |
| Monitoring | Readiness 503 on DB outage/drift, alert delivery, cleanup scheduler, log redaction |
| Load/abuse | Representative concurrency, bounded quotas, outage/replay/load measurements |
| External write | If enabled separately: browser-human approval, provider verification, unknown-outcome reconciliation |
| ARUSIA workers | If enabled separately: authenticated worker, approved assets, real render and QC evidence |

Do not enter account credentials or tokens into GitHub PR descriptions,
public Actions logs or this document.

## Managed PostgreSQL deployment

Prefer `compose.production.managed.yaml`, which starts **only the gateway**
and connects to independently managed PostgreSQL. This avoids using the
bundled development PostgreSQL superuser role as a production runtime role.

Prepare a restricted application account with CONNECT/USAGE/SELECT/INSERT/
UPDATE/DELETE as dictated by verified queries. Use a separate migration
account for DDL. Never run migrations with the live gateway account unless
explicitly assessed. Configure a secrets manager and protected CI/CD secret
injection; environment variables below are *placeholders*.

```bash
# Build and scan image in a controlled CI pipeline, publish to a
# trusted registry, then inject the approved digest-pinned reference.
export IM19_IMAGE_REF='ghcr.io/OWNER/REPOSITORY@sha256:<APPROVED_DIGEST>'

# A deployment uses ONLY that immutable image and a managed DB.
docker compose -f compose.production.managed.yaml config --quiet
docker compose -f compose.production.managed.yaml pull gateway

# Only after backup + migration rehearsal; use separate migration DB credentials.
docker compose -f compose.production.managed.yaml run --rm gateway alembic upgrade head

# Restore least-privilege runtime DB credentials before serving traffic.
docker compose -f compose.production.managed.yaml run --rm gateway \
    im19-mcp-doctor --mode remote --check-database
docker compose -f compose.production.managed.yaml up -d --no-build gateway
```

The image must come from a reviewed release commit, be scanned for
vulnerabilities, and use its exact OCI digest, not a mutable tag. The
`compose.production.managed.yaml` file no longer includes `build:`; it
cannot silently rebuild unreviewed code on a production host.

The management command and Docker Compose obtain secrets from the protected
host environment, not from repository files. Avoid `docker compose config`
without `--quiet` when secrets are populated: it may display them.

Provide a trusted HTTPS reverse proxy. The gateway listens on host loopback
only; add proxy request-body cap (for example 1 MiB for /mcp, much smaller for
browser approval forms), connection limits, actual client-IP quota and query
redaction. Do not expose container port 8000 directly to the Internet.

The `/health` route is liveness only; `/ready` requires a reachable and
migrated database. Verify outage behavior at the load-balancer level.

## Repository rulesets — manual administrator action

Current GitHub API returned `main.protected=false` and an empty rulesets
collection on 28 October 2026. The available GitHub connector has **no
branch-ruleset mutation action**. A repository administrator must configure
Rules → Rulesets with:

- Require pull request and at least one non-author approval for `main`.
- Require CI, Security, PostgreSQL Integration and Codespaces OAuth checks.
- Require branches to be up to date before merge; block force-push/deletion.
- Resolve conversations before merge and disallow bypass for normal contributors.
- Configure repository Environment approval for production deployments.

Do not treat a `CODEOWNERS` file alone as branch protection.

## Staging acceptance sequence

1. Deploy immutable image in staging with feature flags disabled.
2. Confirm /ready, Host/Origin security, OAuth issuer discovery, introspection,
   MCP initialize, tools/list and a read-only user-scoped provider operation.
3. Verify denial/replay/CSRF with non-production test accounts.
4. Rehearse rolling restart, DB loss, secret invalidation, restore and alert delivery.
5. Record the image SHA, endpoint (non-secret), date, test account class, and
   redacted evidence locations outside the repository.
6. Obtain separate operator, security, and data-protection sign-offs.
7. Only after all gates, promote the *same immutable image digest*.

## Known limits

- This branch addresses approval-body denial-of-service, and offers a
  managed-database deployment path. It does **not** establish live OAuth,
  managed key custody, backup drills, penetration testing, real 24/7 operation
  or ChatGPT/mobile connectivity.
- The ARUSIA/autonomous PRs are not included in this branch and require
  their own worker/media acceptance.
- Deployment is NO-GO until the above checks are completed on the real host.


## Live endpoint smoke CLI (after deploying to staging)

A dedicated `im19-production-smoke` command validates the HTTPS MCP
resource, issuer discovery/PKCE, database readiness, rejection of anonymous
MCP, Host/Origin controls and (when explicitly provided) authenticated
initialize/tools/list/get_connection_status. It is **read-only** and prints
only fixed boolean fields and a status. Its output always includes
`production_acceptance=false`: operator sign-off remains mandatory.

```bash
# Discovery, database readiness and denial paths only. Exit 0 means PUBLIC
# PREFLIGHT, not login success:
im19-production-smoke --mcp-resource 'https://mcp.example.com/mcp' \
  --issuer 'https://identity.example.com/realms/im19' --public-only

# Authenticated smoke: acquire a short-lived TEST token from your own issuer,
# inject securely into the process environment, and prevent shell history/logs.
# The command never prints the token. Without one it exits nonzero.
im19-production-smoke --mcp-resource 'https://mcp.example.com/mcp' \
  --issuer 'https://identity.example.com/realms/im19'
```

The authenticated command reads `IM19_MCP_TEST_TOKEN` from its environment.
Do not place a bearer token in command-line arguments, a GitHub Actions
workflow input, issue comments or screenshots. The probe does not perform
Google consent, Drive writes, backup restoration, penetration testing or
ChatGPT plugin setup. Its results cannot replace those release gates.


## Superseding baseline — 5 November 2026

The following status supersedes historical repository references above for the
IM19 release program:

- Current `main`: `bbfd46c2e6e3ae170f74f87e2a03431ae102e107`.
- PR #33 is merged and contains the first outbound MCP router runtime slice.
- PR #21 remains open but is diverged from current `main` (15 commits ahead,
  39 behind); do not merge it without a controlled rebase and fresh review.
- Main branch metadata reports `protected=false`; the rulesets collection is
  empty. GitHub integration access to detailed branch-protection settings was
  denied (403), so administrator configuration remains an external blocker.
- Issue #22 (deployment acceptance) and Issue #32 (outbound MCP router) remain
  open.

### Additional outbound MCP router gates

The router is not considered active simply because `upstream_mcp.py` is
present. The stock gateway entry point currently does not create or inject a
connector registry, production SecretResolver, or authoritative TenantResolver.
No upstream service is configured by default.

Before enabling the outbound router in staging, require:

1. SecretResolver backed by an approved secret manager, returning short-lived
   credentials only to the connector runtime and never to model context/logs.
2. TenantResolver backed by the verified identity directory; model arguments
   must never select or override tenant identity.
3. Operator-owned immutable connector definitions with fixed HTTPS endpoints,
   exact host allowlists, explicit per-tenant read-only tool allowlists and
   bounded request/result/concurrency settings.
4. Enforced network egress policy that blocks loopback, private, link-local,
   cloud metadata, unapproved ports, redirects and DNS-rebinding paths.
5. One non-production upstream MCP service with authenticated
   `initialize`, `tools/list`, and a successful approved read-only call.
6. Negative tests for wrong tenant, missing/expired credentials, quota outage,
   pre-audit outage, post-audit ambiguity, malformed schema, prompt-injection
   tool output, timeout, oversized output, DNS rebinding and service outage.
7. Proof that UGAI calls IM19 only and has no direct upstream endpoint or
   secret access.

The release smoke CLI is a bounded, read-only diagnostic. It cannot verify
Google account consent, the ChatGPT app installation, provider-specific
business behavior, database PITR, encryption-key recovery, egress firewall,
or production operations.

### Production verdict

**NO-GO** until Issues #22 and #32 have complete, independently reviewed
acceptance evidence. CI success is necessary but not sufficient. Do not enable
provider writes or the upstream router in production as part of this change.
