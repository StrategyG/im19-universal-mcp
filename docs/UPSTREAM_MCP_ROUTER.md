# IM19 outbound MCP router implementation

Status: router core is implemented behind an explicit deployment injection seam. It is disabled when `create_app()` is called without an `UpstreamMCPRouter`. No provider connection or production activation is claimed by this code change.

## Architecture

```text
UGAI remote MCP client
  -> IM19 authenticated /mcp resource
    -> verified subject from token context
    -> operator tenant resolver
    -> immutable ConnectorRegistry snapshot
    -> per-connector read-only tool allowlist
    -> quota admission + redacted audit
    -> upstream MCP Streamable HTTP session
```

## Implemented in this change

- Frozen connector definitions with validated connector IDs, fixed HTTPS endpoints, exact DNS host allowlists, secret-manager references, exact tenant allowlists, explicit read-only tool allowlists, bounded timeouts, result sizes and concurrency.
- An immutable registry snapshot with deterministic per-connector and aggregate revisions.
- An injected `SecretResolver` interface. Only the reference is kept in configuration; the resolved bearer token is used in the HTTP authorization header and never included in logs, tool schemas or results.
- Upstream Streamable HTTP session initialization and bounded paginated `tools/list` discovery.
- Namespaced IM19 tool names in the form `{connector_id}__{upstream_tool_name}`; collisions fail startup.
- Strict, bounded JSON-schema subset validation. Unsupported schema keywords/types are rejected rather than guessed. Dynamic tool signatures expose the allowlisted upstream arguments.
- Tenant enforcement using a resolver over the verified IM19 subject. Model arguments cannot provide or override subject, tenant, connector endpoint or credentials.
- Read-only enforcement: every exposed tool must be operator-allowlisted; upstream `readOnlyHint=false` or `destructiveHint=true` makes startup fail.
- Per-connector semaphore, per-call timeout, bounded arguments/results, quota admission and mandatory STARTED/terminal audit records.
- Pre-audit failure blocks upstream execution. Post-audit failure returns `execution_may_have_completed=true`; callers must reconcile and must not blindly retry.
- Optional router lifecycle integration in `create_gateway()` and `create_app()`. Omitted router means no upstream connectors are loaded.

## Deployment composition

A deployment must explicitly construct the registry and provide organization-specific implementations of:

1. `SecretResolver`: resolve a `secret://...` reference from the approved secret manager.
2. `TenantResolver`: map the verified subject from IM19 token introspection to an authoritative tenant identifier.
3. Connector definitions: operator-owned fixed endpoint, exact hostname, secret reference, tenant allowlist and explicit read-only tool names.

Then inject the router into `create_app(settings, upstream_router=router)`. Do not put connector endpoints, credentials, headers, or transport settings in LLM tool arguments. Do not expose an arbitrary registration tool to the model.

The current `im19-gateway` command does not auto-load a cloud-specific secret manager or tenant directory. The code deliberately does not guess these provider-specific integrations. Until a deployment entrypoint supplies them, upstream routing stays off. A staging composition should be built and tested with the real identity provider and secret manager before any production rollout.

## Required network controls

Hostname allowlisting is not a complete SSRF defense. Production must independently enforce outbound network policy that prevents loopback, private, link-local and cloud metadata access; block unapproved destinations/ports; prevent DNS rebinding; and monitor egress. Use fixed operator-controlled DNS names, validated TLS, no unrestricted redirects, and separate network identity for each upstream connector. The application-level URL check is only one control in a defense-in-depth boundary.

The initial router is read-only. Mutating tools, shell execution, arbitrary filesystem access, provider account linking, publishing, deletion and write approvals are not enabled through this router.

## Tests and acceptance

`tests/test_upstream_mcp.py` exercises endpoint/secret/tenant validation, immutable registry revisions, schema rejection, tool namespace generation, verified subject/tenant enforcement, read-only tool admission, quota checks, bounded schema execution and audit failure semantics.

CI passing these tests proves the local router contracts only. Live acceptance still requires a deployed staging IM19 endpoint, a real upstream MCP provider, real OAuth identity, a production-grade secret manager, a tenant directory, egress controls, failure injection and proof that UGAI uses IM19 exclusively.
