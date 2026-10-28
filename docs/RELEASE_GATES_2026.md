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
# Only after backup + migration rehearsal; run with migration credentials.
docker compose -f compose.production.managed.yaml build
docker compose -f compose.production.managed.yaml run --rm gateway alembic upgrade head

# Restore the runtime credentials before starting traffic.
docker compose -f compose.production.managed.yaml run --rm gateway \
    im19-mcp-doctor --mode remote --check-database
docker compose -f compose.production.managed.yaml up -d gateway
```

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
