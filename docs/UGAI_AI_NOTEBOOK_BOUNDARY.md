# UGAI and AI Notebook Studio integration boundary

**Status:** architecture contract only. No live UGAI or notebook connection is claimed.

## System ownership

- IM19 owns the remote MCP protocol gateway, provider authentication, resource-bound OAuth validation, encrypted provider credential storage, provider quotas, provider-side authorization and audit.
- UGAI is the only system in this three-repository design that orchestrates agents and consumes the IM19 tool provider. UGAI owns workflow-level tool discovery, operator-approved allowlists, decision policy and orchestration provenance.
- AI Notebook Studio owns notebooks, projects, cells, kernels, the execution queue, isolated notebook workers, resource limits and notebook artifacts.

IM19 must not become a notebook execution service. A notebook worker must not connect directly to IM19 or receive provider credentials.

## Expected request path

    AI Notebook Studio UI/API
              |
              | authenticated task request
              v
    UGAI orchestration service
              |
              | resource-bound OAuth / MCP Streamable HTTP
              v
    IM19 MCP gateway
              |
              v
    Approved external provider connectors

For notebook computation, UGAI calls a separate authenticated execution API owned by AI Notebook Studio. That execution path is outside MCP:

    UGAI -> authenticated Studio Execution API -> isolated notebook worker

## Security invariants

1. UGAI receives actor identity from trusted authentication context. It must not trust actor, tenant, role or provider account supplied by model-generated tool arguments.
2. UGAI uses an audience-bound token for the exact IM19 resource and only approved scopes. The Google OAuth relationship inside IM19 is separate from UGAI-to-IM19 authentication.
3. IM19 independently validates token issuer, resource audience, scope, expiry and authenticated subject. A repository URL is not a deployed service endpoint.
4. IM19 credentials, Google refresh tokens, OAuth codes, browser cookies and encryption keys must never be returned to UGAI prompts, Studio, notebook cells, artifacts or logs.
5. Tool discovery is not authorization. Empty-by-default, operator-reviewed allowlists and hard-deny rules remain mandatory.
6. Provider permissions and quotas remain enforced in IM19 even when UGAI performs an earlier workflow policy check.
7. Notebook code execution, cancellation, files and kernel state remain in Studio's isolated runtime. MCP tool execution must not be used as a general-purpose shell or notebook execution proxy.
8. Any future external write requires independently verified, action-bound human approval and idempotent execution. A successful notebook task is not approval for a provider-side write.

## Existing implementation and remaining work

The UGAI branch contains an IM19ToolProvider facade with discovery/execution methods and pre/post redacted audit requirements. UGAI integration documentation states that a real HTTPS deployment, OAuth token provider, durable audit sink and workflow-registry wiring remain required.

IM19 currently documents a read-oriented gateway baseline and an experimental disabled-by-default Drive folder-creation path. This must not be generalized into arbitrary provider write access.

The AI Notebook Studio V6 branch describes a JWT/JWKS identity foundation, but tenant membership, resource authorization and notebook runtime execution are not implemented/verified there. IM19 must not accept Studio-originated notebook execution requests.

## Acceptance gates

- UGAI authenticates to the exact IM19 resource with the correct audience and least-privilege scope.
- Anonymous, expired, wrong-audience, insufficient-scope and cross-tenant requests are denied.
- UGAI allowlists are reviewed, empty by default and narrower than the full discovered tool list.
- Pre-execution audit failure blocks the tool call; post-execution audit failure produces an uncertain outcome requiring reconciliation.
- IM19 enforces provider account ownership, scopes, quota and confirmation requirements.
- No provider secret or credential is exposed to Studio or notebook workers.
- Staging test proves authenticated MCP initialize, discovery and one approved read-only provider call.
- Any write path remains disabled until independent approval, idempotency, audit and recovery gates pass.

This document does not add or enable a new MCP tool, OAuth client, provider mutation, endpoint, deployment or credential.
