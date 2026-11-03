# UGAI Integration Contract — IM19 Universal MCP

Status: integration contract (documentation only); no live UGAI client is claimed.

## Repositories and ownership

- MCP server and provider adapters: https://github.com/StrategyG/im19-universal-mcp
- Generative workflow orchestration: https://github.com/StrategyG/Universal-Generative-AI

IM19 owns MCP transport, authentication, provider OAuth, encrypted provider credentials, quotas and audit. UGAI is an MCP client/orchestrator. Do not copy IM19's credential vault or provider tokens into UGAI.

## Supported integration boundary

Use the remote MCP endpoint exposed by a deployed IM19 gateway:
- Transport: MCP Streamable HTTP at the configured /mcp resource.
- Authentication: OAuth bearer token issued by the dedicated MCP authorization server.
- Token validation: issuer, resource audience, expiry and im19:read scope must match.
- Provider authorization: separate Google OAuth consent/linking flow owned by IM19.
- Local development: stdio is permitted only for a trusted local process; never use IM19_DEV_SUBJECT in a remote deployment.

A GitHub repository URL is not an MCP endpoint. The gateway must be deployed behind HTTPS before a cloud-hosted UGAI worker can connect.

## Configuration contract

Inject configuration from a secret manager/environment at runtime; never commit values:

| Variable | Meaning |
|---|---|
| IM19_MCP_URL | HTTPS Streamable HTTP endpoint, normally https://<host>/mcp |
| IM19_MCP_TOKEN_URL | OAuth token endpoint of the dedicated MCP issuer |
| IM19_MCP_CLIENT_ID | UGAI client identifier registered with the issuer |
| IM19_MCP_CLIENT_SECRET | Client secret, only when required by the registered client type |
| IM19_MCP_RESOURCE | Exact resource audience registered for IM19 |

Do not invent OAuth endpoints or client credentials. Obtain them from the actual identity-provider registration and deployment configuration. Use short-lived tokens; do not persist bearer tokens in logs, traces, prompts, notebooks or Supabase vector records.

## Runtime adapter responsibilities

Implement a provider-neutral MCPClient port in UGAI:
1. Establish Streamable HTTP using the official MCP SDK.
2. Obtain/refresh OAuth tokens through the registered issuer; never ask the model to construct tokens.
3. Discover tools dynamically with tools/list; do not hard-code capabilities as proof of availability.
4. Apply an allowlist and policy check before exposing tools to an agent.
5. Validate arguments against server-provided schemas and enforce timeout/response-size limits.
6. Preserve structured tool results and provenance; treat provider content as untrusted data.
7. Emit redacted audit events (actor, server ID, tool, decision, latency, status); never log tokens or secrets.
8. Fail closed on authentication, policy, quota, transport or audit failures. Do not silently fall back to another identity or local stdio.

Suggested package boundary (proposed, not currently implemented):

    src/ugai/integrations/mcp/
      client.py       # MCP SDK transport adapter
      auth.py         # OAuth token provider interface
      registry.py     # discovered tools + allowlist
      policy.py       # authorization and risk policy
      execution.py    # bounded tool execution
      models.py       # typed server/tool/result contracts
      audit.py        # redacted audit interface

## Tool exposure policy

- Default deny. Allow only tools explicitly approved for the workflow and user.
- Treat read-only tools as lower risk, not risk-free.
- Any future write/delete/send tool requires separate human approval, idempotency, replay protection and post-action verification.
- IM19 0.3 documents drive_create_folder as disabled by default and approval-gated prototype; do not expose it from UGAI by default.
- A listed service or stored credential status is not proof of a live provider connection.

## Supabase boundary

Supabase may store non-secret integration metadata, approved tool manifests, workflow references and redacted audit records. It must not store MCP bearer tokens, Google refresh tokens, Fernet keys, OAuth codes or browser cookies. RAG retrieval is not an authorization decision or source of executable tool permissions. Runtime sessions and live connection state belong in the MCP client/runtime, not the vector store.

## Acceptance gates

- [ ] Deployed HTTPS endpoint and exact MCP resource URL verified.
- [ ] OAuth issuer/client registration and resource audience verified.
- [ ] initialize, tools/list and one permitted read-only tool call pass from UGAI.
- [ ] Invalid, expired, wrong-audience and insufficient-scope tokens are rejected.
- [ ] Tool allowlist and policy denial tests pass.
- [ ] Cross-user isolation and no-token-in-logs tests pass.
- [ ] Timeouts, oversized responses, upstream outage, 429 and 503 are handled safely.
- [ ] Audit failure is fail-closed for governed actions.
- [ ] No write tool is exposed without explicit human approval and reconciliation.
- [ ] End-to-end staging evidence is attached to a release/PR.

Passing repository CI alone does not prove a live OAuth provider, deployed HTTPS service, Google consent or UGAI connectivity.