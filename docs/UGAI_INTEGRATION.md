# UGAI Integration Contract — IM19 Universal MCP

Status: integration contract plus reusable UGAI remote MCP adapter on branch `integration/im19-mcp-adapter-v1`; live connection remains unverified.

## Repositories and ownership

- MCP server and provider adapters: https://github.com/StrategyG/im19-universal-mcp
- Generative workflow orchestration: https://github.com/StrategyG/Universal-Generative-AI
- UGAI adapter implementation: https://github.com/StrategyG/Universal-Generative-AI/pull/6

IM19 owns MCP transport, authentication, provider OAuth, encrypted provider credentials, quotas and provider-side audit. UGAI is an MCP client/orchestrator. Do not copy IM19's credential vault or provider tokens into UGAI.

## Supported integration boundary

Use the remote MCP endpoint exposed by a deployed IM19 gateway:
- Transport: MCP Streamable HTTP at the configured `/mcp` resource.
- Authentication: short-lived OAuth bearer token issued by the dedicated MCP authorization server.
- Token contract: request the exact configured IM19 resource audience and `im19:read` scope; IM19 validates issuer, resource audience, expiry, scope and subject through token introspection.
- Provider authorization: separate Google OAuth consent/linking flow owned by IM19.
- Local stdio is permitted only for trusted local development; never use a development subject in remote deployment.

A GitHub repository URL is not an MCP endpoint. The gateway must be deployed behind HTTPS before a cloud-hosted UGAI worker can connect.

## Implemented adapter boundary

The UGAI branch `integration/im19-mcp-adapter-v1` contains `src/ugai_mcp/`:
- `config.py`: HTTPS endpoint, exact `/mcp` path, exact DNS hostname allowlist, same-host resource, finite timeout and response-size validation.
- `auth.py`: injected `AccessTokenProvider` contract for exact resource and `im19:read` scope; concrete OAuth client is deployment-specific.
- `client.py`: official MCP Python SDK Streamable HTTP session, initialize, dynamic tools/list and bounded tool calls.
- `runtime.py`: framework-neutral `IM19ToolProvider` facade for discover/execute integration with a host workflow registry.
- `audit.py`: redacted audit event contract; durable `AuditSink` implementation is supplied by deployment.
- `policy.py`: default deny, operator-reviewed allowlist and hard-deny rules for privileged tools.
- `tests/`: config, policy, scope and audit lifecycle regression tests.

### Tool policy compatibility

The inspected IM19 0.3 `src/im19_mcp/server.py` registers tools without MCP read-only annotations. The UGAI adapter therefore does not infer safety from annotations. The allowlist is empty by default and must be approved by an operator. If annotations are present, non-read-only or destructive hints deny the tool.

Conservative initial allowlist candidates for review:
- `list_available_services`, `get_service_capabilities`, `get_connection_status`
- `youtube_get_channel`, `youtube_list_videos`, `youtube_get_analytics`
- `drive_search_files`, `drive_list_folder`, `drive_get_metadata`

Hard-denied even if added to the allowlist: `request_service_authorization`, `confirm_service_authorization`, `disconnect_service`, `drive_read_file`, `drive_create_folder`, `drive_get_write_approval_status`, `cloudbrowser_get_task`, and `cloudbrowser_list_tasks`.

### Audit contract

A durable audit write is mandatory before and after every remote tool call. Pre-call audit failure prevents execution. Post-call audit failure is marked as potentially completed; callers must reconcile by request ID and must not blindly retry. Audit events exclude tool arguments, provider results and credentials.

### Network boundary

The endpoint and OAuth resource must use the same exact allowlisted DNS hostname. Wildcards, IP literals, non-default ports and URL credentials/query/fragment are rejected. This is only an application-level host guardrail; production egress policy must block loopback, private, link-local and metadata destinations and mitigate DNS rebinding.

The adapter is not yet wired into a deployed UGAI agent/workflow runtime. This repository branch has no production workflow registry to attach automatically. A deployment-specific OAuth provider, durable audit sink, application wiring and live staging evidence remain required.

## Supabase boundary

Supabase may store non-secret integration metadata, approved tool manifests, workflow references and redacted audit records. It must not store MCP bearer tokens, Google refresh tokens, encryption keys, OAuth codes or browser cookies. RAG retrieval is not an authorization decision or source of executable tool permissions.

## Acceptance gates

- [ ] Deployed HTTPS endpoint and exact MCP resource URL verified.
- [ ] Egress policy blocks private/link-local/metadata destinations and DNS rebinding is tested.
- [ ] OAuth issuer/client registration and resource audience verified.
- [ ] Token provider requests exact `im19:read` scope and resource; invalid, expired, wrong-audience and insufficient-scope tokens are rejected.
- [ ] Durable audit sink is implemented; outage tests prove fail-closed behavior and uncertain-completion reconciliation.
- [ ] `IM19ToolProvider.discover/execute` is attached to the deployed UGAI workflow registry.
- [ ] initialize, tools/list and one permitted read-only provider call pass from UGAI.
- [ ] Cross-user isolation, no-token-in-logs, timeout, oversized-response, 429 and 503 tests pass.
- [ ] End-to-end staging evidence is attached to a release/PR.

Passing repository CI alone does not prove a live OAuth provider, deployed HTTPS service, Google consent or UGAI connectivity.
