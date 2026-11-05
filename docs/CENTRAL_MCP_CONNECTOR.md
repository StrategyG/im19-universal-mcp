# IM19 Central MCP Connector

Status: design contract only. This document does not claim that upstream MCP proxying or provider connectivity is implemented.

## Mandatory route

All MCP tools used by StrategyG/Universal-Generative-AI must be reached through the authenticated IM19 remote MCP endpoint. UGAI must not open direct sessions to external MCP providers.

```text
UGAI -> IM19ToolProvider -> IM19 HTTPS MCP gateway
    -> verified principal / tenant
    -> connector registry and tool policy
    -> approval, quota, redacted audit
    -> registered upstream MCP service
```

IM19 is the only MCP control-plane entry point for UGAI. A connector must never be selected by an arbitrary URL or credential supplied in a model-generated argument.

## Responsibility boundary

UGAI owns workflow orchestration, workflow-specific allowlists, and execution provenance. IM19 owns upstream connector registration, authentication, provider credentials, endpoint restrictions, tenant authorization, approvals, quotas, audit, and upstream transport.

The authenticated subject and tenant must come from verified IM19 token context. Never accept them from tool arguments.

## Connector registry contract

Each operator-managed connector record must define a stable ID, fixed HTTPS endpoint, approved hostname and port, transport, secret-manager reference, enabled state, tenant scope, explicit tool allowlist, read/mutate classification, input/output bounds, timeout, concurrency and rate limits, owner, and configuration revision.

Do not store provider secrets in the registry. Resolve credentials from an approved secret manager at execution time. Reject URL credentials, unapproved redirects, private, loopback, link-local and metadata destinations, unapproved ports, and DNS rebinding. Enforce network egress policy outside the application too; hostname validation alone is not an SSRF defense.

## Namespaced discovery

Expose tools using stable names such as github__*, context7__*, playwright__*, postman__*, and chrome_devtools__*. Reject collisions. Upstream tool descriptions, schemas, and results are untrusted data. Validate schemas and enforce IM19 policy regardless of upstream annotations.

Default deny. Begin with operator-approved read-only tools only. Mutating tools require a one-time, expiring approval bound to the verified subject, tenant, connector, tool, and normalized request digest. Deletion, publishing, account linking, shell execution, and unrestricted filesystem access remain disabled until separately reviewed.

## Execution lifecycle

1. Verify token issuer, audience, expiry, subject, tenant and scopes.
2. Resolve tool name against an immutable registry revision.
3. Enforce connector, tool, tenant, quota and policy checks.
4. Validate arguments and enforce size/time/concurrency limits.
5. Obtain human approval for mutations.
6. Persist a redacted STARTED audit event; fail closed if audit is unavailable.
7. Call the registered upstream MCP endpoint using least-privilege credentials.
8. Validate and bound the result; treat returned content as untrusted.
9. Persist a terminal audit event. If this fails, report uncertain completion and require reconciliation; never blindly retry.

Audit records may include request ID, pseudonymous subject, tenant, connector, tool, registry revision, outcome, duration, bounded error code and approval reference. Never log tokens, authorization headers, refresh tokens, raw secrets, or unrestricted arguments/results.

## DaVinci Resolve local worker

DaVinci Resolve MCP is local and must not be exposed as a public inbound endpoint. A future Local MCP Worker on the editing workstation must establish an outbound authenticated connection to IM19, pull short-lived scoped jobs, invoke local Resolve MCP through stdio or loopback, and return results plus audit metadata. Restrict tool access and filesystem roots. Require human approval for destructive timeline, media, render, overwrite, delete or publish actions. This worker is separate new work and is not implemented by this document.

## Initial rollout order

1. GitHub MCP in read-only mode and restricted to approved repositories.
2. Context7 documentation lookup.
3. Playwright in an isolated browser and approved test origins.
4. Chrome DevTools in an isolated browser profile.
5. Postman minimal toolset in a non-production workspace.
6. DaVinci Resolve only after the Local MCP Worker passes security review.

Pin connector versions and record license, maintainer, owner, credential scope, and operational health for every provider.

## Acceptance gates

- Implement typed registry, immutable revisions, upstream Streamable HTTP client, namespaced discovery, collision handling, policy checks and result bounds.
- Integrate verified identity, tenant isolation, approval, quotas and durable audit.
- Add secret-manager integration and egress restrictions.
- Test SSRF, DNS rebinding, redirects, tenant isolation, tool spoofing, schema abuse, oversized results, timeouts, replay, audit outage and upstream outage.
- Test against a deployed staging IM19 endpoint and at least one non-production upstream MCP using real OAuth.
- Build and security-scan the Local MCP Worker separately before enabling DaVinci.
- Complete monitoring, backup/restore, rollback and independent production review.

Until these gates pass, status must remain NOT LIVE / NOT PRODUCTION ACCEPTED.
