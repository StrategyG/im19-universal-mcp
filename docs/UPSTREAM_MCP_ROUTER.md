# IM19 outbound MCP router — runtime wiring candidate v1

**Status: implementation candidate on `feat/im19-upstream-runtime-wiring-v1`; not merged or deployed.** The existing router core is in `main`; this branch wires an opt-in operator configuration, secret reference resolver and database-backed tenant resolver. No live upstream MCP provider or production secret manager has been connected or verified.

## Runtime path

```text
UGAI -> IM19 authenticated Streamable HTTP
     -> verified introspected subject
     -> active subject-to-tenant binding in PostgreSQL
     -> immutable operator ConnectorRegistry
     -> secret://env reference resolved at runtime
     -> fixed HTTPS upstream + exact host allowlist
     -> read-only allowlisted MCP tool
     -> quota admission + redacted audit
     -> UNTRUSTED_EXTERNAL_DATA result
```

## Candidate implementation

- `src/im19_mcp/upstream_runtime.py` parses bounded `IM19_UPSTREAM_CONNECTORS_JSON`, rejects duplicate/unknown keys and raw credential fields, and constructs immutable `ConnectorDefinition` entries.
- `EnvironmentSecretResolver` only accepts `secret://env/NAME`. Deployment secret management must inject the actual value as `IM19_UPSTREAM_SECRET_NAME`; connector JSON contains the reference only. This adapter is not a direct cloud KMS/Vault integration.
- `PostgresTenantResolver` resolves the verified MCP subject against `mcp_subject_tenant_bindings` with `status='active'`; database failure or no active binding denies execution. The current schema supports one tenant per subject. Multi-tenant selection is not implemented.
- `create_app()` loads the configured router at startup if an explicit router was not injected. No config or no enabled entries means the router remains off. Enabled connectors must resolve secrets and pass upstream initialization before startup completes.
- The Alembic candidate `0007_upstream_tenant_bindings` creates the operator-managed subject/tenant binding. Readiness now requires this schema head.

## Operator configuration

Set `IM19_UPSTREAM_CONNECTORS_JSON` only in a protected deployment environment. Example (disabled by default):

~~~json
[
  {
    "connector_id": "github",
    "endpoint": "https://mcp.github.example/mcp",
    "allowed_hosts": ["mcp.github.example"],
    "secret_ref": "secret://env/GITHUB_MCP",
    "read_only_tools": ["search_repositories"],
    "tenant_allowlist": ["tenant-a"],
    "enabled": false
  }
]
~~~

The corresponding credential is injected separately as `IM19_UPSTREAM_SECRET_GITHUB_MCP`. Never put tokens in connector JSON, repository files, issues, PR descriptions, logs or tool arguments.

After applying the approved migration to a staging database, the operator must bind the verified identity subject to one tenant through a reviewed administrative procedure. The migration intentionally does not seed subjects or tenants. No identity should gain access merely because it exists in the registry.

## Security boundaries

- Only fixed HTTPS endpoints, exact allowed DNS hosts, bounded timeouts/results/concurrency and exact read-only tool names are permitted.
- Upstream tool annotations must declare read-only and not destructive. Dynamic schemas are validated; identity/secret arguments are prohibited.
- The connector's tenant allowlist and the database's active subject binding must both permit the call.
- Quota and pre-audit failures deny the upstream call. If terminal audit fails after an upstream call, result is marked uncertain and must be reconciled; never blind retry.
- Provider tool descriptions/defaults are not trusted instructions. External results are labeled `UNTRUSTED_EXTERNAL_DATA` and must remain data in UGAI.
- Application endpoint validation is not a complete SSRF defense. Production must add egress firewall policy blocking loopback, private, link-local, metadata IPs, unapproved ports, redirects and DNS rebinding, with monitoring.
- Only read-only upstream MCP tools are supported in this release. Mutations, shell access and arbitrary filesystem tools are not enabled.

## Required staging acceptance

1. Apply the numbered migration in a staging copy after a verified backup and confirm readiness at `0007_upstream_tenant_bindings`.
2. Provision a dedicated, least-privilege database login and a reviewed active subject/tenant binding.
3. Inject a short-lived test connector token using the approved deployment secret manager.
4. Configure one non-production upstream MCP endpoint with a small read-only allowlist.
5. Verify upstream `initialize`, `tools/list`, and a read-only tool call through IM19; no direct UGAI-to-provider route is allowed.
6. Prove wrong tenant, disabled binding, missing/expired secret, quota outage, pre-audit outage, post-audit ambiguity, timeout, oversized response, malformed schema and upstream outage all fail closed.
7. Test DNS rebinding and egress restrictions at the network layer.
8. Record exact SHA, migration revision, endpoint class, test timestamp, redacted logs and independent QA reviewer.

The current candidate CI tests cover local configuration, tenant resolution, async router behavior and existing router contracts. They do not prove live identity, an external secret manager, upstream provider connectivity, network egress policy or production operations.

## Production verdict

**NO-GO.** Branch is not merged/deployed; migration is not applied; no secret manager, active tenant bindings, upstream provider, network egress policy, live UGAI integration, DR restore or operator sign-off is evidenced.
