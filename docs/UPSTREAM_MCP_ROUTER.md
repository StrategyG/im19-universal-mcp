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
- One connector is bound to exactly one tenant; deploy a distinct connector ID and secret reference for each tenant to prevent shared provider credentials crossing tenant boundaries. Tool schemas containing identity/tenant or credential arguments (for example `subject`, `tenant_id`, `user_id`, `access_token` or `api_key`) are rejected.
- An immutable registry snapshot with deterministic per-connector and aggregate revisions.
- An injected `SecretResolver` interface. Only the reference is kept in configuration; the resolved bearer token is used in the HTTP authorization header and never included in logs, tool schemas or results.
- Upstream Streamable HTTP session initialization and bounded paginated `tools/list` discovery.
- Namespaced IM19 tool names in the form `{connector_id}__{upstream_tool_name}`; collisions fail startup.
- Strict, bounded JSON-schema subset validation. Unsupported schema keywords/types are rejected rather than guessed. Dynamic tool signatures expose the allowlisted upstream arguments.
- Tenant enforcement using a resolver over the verified IM19 subject. Model arguments cannot provide or override subject, tenant, connector endpoint or credentials.
- Read-only enforcement: every exposed tool must be operator-allowlisted; upstream `readOnlyHint=false` or `destructiveHint=true` makes startup fail.
- Per-connector semaphore, per-call timeout, bounded arguments/results, quota admission and mandatory STARTED/terminal audit records.
- Staging and production fail closed when the distributed quota backend is unavailable; only isolated local development may omit it.
- Pre-audit failure blocks upstream execution. Post-audit failure returns `execution_may_have_completed=true`; callers must reconcile and must not blindly retry.
- Successful upstream payloads carry `result_classification=UNTRUSTED_EXTERNAL_DATA`. Preserve this label through UGAI task state and user-visible provenance; external text is data, never executable instruction. UGAI must enforce this boundary in its own orchestration/prompt policy.
- Upstream tool descriptions, property descriptions, and defaults are not forwarded as model-visible metadata. IM19 emits a fixed gateway-authored description and omits provider-supplied argument defaults, because connector metadata is also an untrusted prompt-injection surface. Operators should maintain trusted local documentation for approved tool semantics.
- Optional router lifecycle integration in `create_gateway()` and `create_app()`. Omitted router means no upstream connectors are loaded.

## Deployment composition

A deployment must explicitly construct the registry and provide organization-specific implementations of:

1. `SecretResolver`: resolve a `secret://...` reference from the approved secret manager.
2. `TenantResolver`: map the verified subject from IM19 token introspection to an authoritative tenant identifier.
3. Connector definitions: operator-owned fixed endpoint, exact hostname, secret reference, tenant allowlist and explicit read-only tool names.

Then inject the router into `create_app(settings, upstream_router=router)`. Do not put connector endpoints, credentials, headers, or transport settings in LLM tool arguments. Do not expose an arbitrary registration tool to the model.

### Built-in bounded deployment adapter

A fail-closed operator composition is available through these deployment-only settings:

- `IM19_UPSTREAM_CONNECTORS_JSON`: JSON list of immutable connector definitions. It contains endpoint/host allowlists, exact read-only tool names, tenant allowlist, `enabled`, and a `secret_ref`; it must never contain a token or credential.
- `IM19_UPSTREAM_TENANT_MAP_JSON`: exact JSON object mapping an already introspected IM19 subject to one tenant identifier. Wildcards and unknown subjects are denied.
- `IM19_UPSTREAM_SECRET_ROOT`: read-only mounted secret directory; default `/run/secrets/im19/upstream`.

Example shape (synthetic values only):

```json
[
  {
    "connector_id": "github",
    "endpoint": "https://mcp.github.example/mcp",
    "allowed_hosts": ["mcp.github.example"],
    "secret_ref": "secret://production/github/mcp-token",
    "read_only_tools": ["search_repositories"],
    "tenant_allowlist": ["tenant-a"],
    "enabled": false
  }
]
```

```json
{
  "verified-oidc-subject-123": "tenant-a"
}
```

The secret resolver reads `secret://production/github/mcp-token` from
`/run/secrets/im19/upstream/production/github/mcp-token`. It refuses path traversal,
symlinks, non-regular files, over-sized secret files and group/world-writable secret files.
Mount the directory read-only from the platform secret manager; do not copy secret bytes
into the environment, connector JSON, image or repository.

If both JSON documents are absent, the upstream router remains disabled. If only one is
present, malformed JSON, duplicate keys, unrecognized connector fields, invalid tenant
mappings or an unavailable secret root are detected before the gateway starts. An explicit
injected `upstream_router` remains supported for deployment-specific composition.

**Deployment maturity boundary:** this built-in exact subject map is a bounded bootstrap
adapter for isolated staging or a small operator-managed deployment. It is static and
requires a reviewed redeploy to change membership; it is not a substitute for a live
revocation-aware institutional tenant directory. Production activation still requires
a production secret manager mount, real identity-provider subject inventory, dynamic
tenant/membership lifecycle, network egress enforcement, and end-to-end staging evidence.
The built-in adapter does not activate any connector by default.

The current `im19-gateway` command does not auto-load a cloud-specific secret manager or
live tenant directory. Until the operator supplies and validates those deployment
dependencies, upstream routing stays off.

## Required network controls

Hostname allowlisting is not a complete SSRF defense. Production must independently enforce outbound network policy that prevents loopback, private, link-local and cloud metadata access; block unapproved destinations/ports; prevent DNS rebinding; and monitor egress. Use fixed operator-controlled DNS names, validated TLS, no unrestricted redirects, and separate network identity for each upstream connector. The application-level URL check is only one control in a defense-in-depth boundary.

The initial router is read-only. Mutating tools, shell execution, arbitrary filesystem access, provider account linking, publishing, deletion and write approvals are not enabled through this router.

## Tests and acceptance

`tests/test_upstream_mcp.py` exercises endpoint/secret/tenant validation, immutable registry revisions, schema rejection, tool namespace generation, verified subject/tenant enforcement, read-only tool admission, quota checks, bounded schema execution and audit failure semantics.

CI passing these tests proves the local router contracts only. Live acceptance still requires a deployed staging IM19 endpoint, a real upstream MCP provider, real OAuth identity, a production-grade secret manager, a tenant directory, egress controls, failure injection and proof that UGAI uses IM19 exclusively.
