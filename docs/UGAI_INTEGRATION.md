# UGAI Integration Contract — IM19 Universal MCP

Status: integration contract plus a reusable UGAI client adapter on branch `integration/im19-mcp-adapter-v1`; live connection remains unverified.

## Repositories and ownership

- MCP server and provider adapters: https://github.com/StrategyG/im19-universal-mcp
- Generative workflow orchestration: https://github.com/StrategyG/Universal-Generative-AI
- UGAI adapter implementation: https://github.com/StrategyG/Universal-Generative-AI/pull/6

IM19 owns MCP transport, authentication, provider OAuth, encrypted provider credentials, quotas and audit. UGAI is an MCP client/orchestrator. Do not copy IM19's credential vault or provider tokens into UGAI.

## Supported integration boundary

Use the remote MCP endpoint exposed by a deployed IM19 gateway:
- Transport: MCP Streamable HTTP at the configured `/mcp` resource.
- Authentication: short-lived OAuth bearer token issued by the dedicated MCP authorization server.
- Token validation: issuer, resource audience, expiry and required read scope must match.
- Provider authorization: separate Google OAuth consent/linking flow owned by IM19.
- Local stdio is permitted only for trusted local development; never use a development subject in remote deployment.

A GitHub repository URL is not an MCP endpoint. The gateway must be deployed behind HTTPS before a cloud-hosted UGAI worker can connect.

## Implemented adapter boundary

The UGAI branch `integration/im19-mcp-adapter-v1` contains `src/ugai_mcp/`:
- `config.py`: HTTPS endpoint, exact path, resource, timeout and response-size validation.
- `auth.py`: injected `AccessTokenProvider` interface; concrete OAuth client is deployment-specific.
- `client.py`: official MCP Python SDK Streamable HTTP session, initialize, dynamic tools/list and bounded tool calls.
- `policy.py`: default deny; explicit allowlist, server read-only attestation and non-destructive annotation.
- `tests/`: policy and configuration unit tests.

The adapter is not yet wired into a UGAI agent/workflow runtime. A redacted audit sink and live end-to-end staging evidence remain required.

## Supabase boundary

Supabase may store non-secret integration metadata, approved tool manifests, workflow references and redacted audit records. It must not store MCP bearer tokens, Google refresh tokens, encryption keys, OAuth codes or browser cookies. RAG retrieval is not an authorization decision or source of executable tool permissions.

## Acceptance gates

- [ ] Deployed HTTPS endpoint and exact MCP resource URL verified.
- [ ] OAuth issuer/client registration and resource audience verified.
- [ ] Deployment-specific token provider implemented and tested.
- [ ] initialize, tools/list and one permitted read-only tool call pass from UGAI.
- [ ] Invalid, expired, wrong-audience and insufficient-scope tokens are rejected.
- [ ] Tool allowlist and policy denial tests pass in CI.
- [ ] Cross-user isolation and no-token-in-logs tests pass.
- [ ] Timeouts, oversized responses, upstream outage, 429 and 503 are handled safely.
- [ ] Redacted audit events are recorded; audit failure fails closed for governed actions.
- [ ] No write tool is exposed without explicit human approval and reconciliation.
- [ ] End-to-end staging evidence is attached to a release/PR.

Passing repository CI alone does not prove a live OAuth provider, deployed HTTPS service, Google consent or UGAI connectivity.
