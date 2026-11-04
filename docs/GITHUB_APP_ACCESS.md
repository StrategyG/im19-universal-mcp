# GitHub App Access Contract for AI Models

Status: **Architecture specification / implementation not verified**  
Version: 1.0.0  
Owner: IM19 Universal MCP  
Related systems: UGAI, GitHub App, AI Notebook Studio, REVORA IDE, Cloudbrowser

## 1. Objective

Provide ChatGPT, Codex, Claude, Gemini, and approved self-hosted models with one consistent, auditable route to read and contribute to explicitly installed StrategyG repositories.

The integration MUST NOT distribute a human user's personal access token or GitHub App private key to any model, prompt, notebook, browser session, or client application.

## 2. Target architecture

```text
AI clients / models
  -> UGAI (reasoning, planning, task ownership)
  -> IM19 authenticated MCP gateway (identity, policy, authorization, audit)
  -> GitHub App installation token broker (server-side only)
  -> GitHub REST / Git Data APIs
  -> allowlisted StrategyG repositories
```

UGAI decides what work should be performed. IM19 authorizes each operation and owns the credential boundary. GitHub remains the source of truth for repository content and pull requests. Cloudbrowser may execute isolated build/test jobs, but must not receive broad GitHub credentials.

## 3. GitHub App registration

Recommended app name: `UGAI AI Engineering`  
Owner: `StrategyG`  
Installation mode: only selected repositories  
Callback and webhook URLs: deployment-specific HTTPS endpoints; do not invent or register placeholders.  
Private key: generated and stored in an approved secret manager; never committed.

### 3.1 Minimum permissions

| Permission | Level | Purpose |
|---|---|---|
| Metadata | Read | Repository identity and installation metadata |
| Contents | Read & write | Read files; write commits and branches |
| Pull requests | Read & write | Open/update PRs and read review state |
| Issues | Read & write | Optional issue-based work tracking |
| Checks | Read | Inspect CI checks |
| Commit statuses | Read | Inspect commit status |
| Actions | Read | Inspect workflow runs where required |

Do not grant Administration, Secrets, Environments, Members, Webhooks administration, or Workflows write permission by default. Add a permission only with a documented use case and a separate security review. If CI workflow files must be changed, treat that as a privileged operation requiring explicit human approval and a separate permission decision.

### 3.2 Installation scope

Initially select only these existing repositories:

- `Universal-Generative-AI`
- `im19-universal-mcp`
- `RAG_Cerdas`
- `ai-notebook-studio-v`
- `Cloudbrowser`
- `android-ai-universal-connector`
- `REVORA_IDE`

Add `ugai-platform-contracts` and `ugai-platform-admin` only after those repositories have actually been created and reviewed. Never assume they exist.

## 4. Authentication and token handling

1. IM19 authenticates the human or service caller using its configured issuer.
2. IM19 resolves the verified subject to an authoritative tenant and role.
3. The server-side GitHub App broker creates an installation access token for the relevant installation.
4. The broker requests only the minimum repository subset and permissions needed for the operation.
5. The token remains server-side, is short-lived (GitHub installation tokens are normally valid for up to one hour), and is never returned to the model.
6. IM19 redacts authorization headers, private keys, token values, webhook secrets, and sensitive provider output from logs.
7. Revocation/removal of the GitHub App installation must immediately stop subsequent operations.

Never accept a token, private key, owner, installation ID, repository URL, arbitrary API URL, or tenant ID supplied by an LLM as authority. These values must be resolved from trusted server-side configuration and verified identity.

## 5. MCP tool contract

Expose a narrow, typed tool surface. Tool names below are proposed contract names, not a claim that they are implemented.

### Read operations

- `github.list_repositories`
- `github.get_repository`
- `github.get_tree`
- `github.search_code`
- `github.read_file`
- `github.list_branches`
- `github.get_commit`
- `github.list_pull_requests`
- `github.get_pull_request`
- `github.read_checks`

### Contribution operations

- `github.create_branch`
- `github.create_or_update_file`
- `github.create_commit`
- `github.open_pull_request`
- `github.update_pull_request`
- `github.create_issue_comment`

### Privileged operations

- `github.merge_pull_request`
- `github.modify_branch_protection`
- `github.modify_repository_settings`
- `github.modify_workflows`

Privileged operations MUST be unavailable to ordinary model identities. Merge must require an explicit, action-bound human approval receipt, fresh policy evaluation, passing required checks, and a verified PR head SHA. Repository administration and workflow modification remain disabled unless separately authorized.

## 6. Authorization model

Roles:

- `AI_READER`: read only.
- `AI_CONTRIBUTOR`: read and contribute to non-protected working branches; create/update pull requests.
- `AI_RELEASE_APPROVER`: inspect release evidence and request a human approval; this role does not itself bypass branch protection or replace GitHub's required review.

Authorization is evaluated for every call using:

- verified subject and tenant;
- GitHub App installation and repository allowlist;
- role and tool permission;
- repository and branch policy;
- action risk classification;
- approval receipt, if required;
- idempotency key and request correlation ID.

The model cannot elevate its own role, alter its tenant, select an uninstalled repository, or override branch protection through tool arguments.

## 7. Branch and change policy

- All AI writes go to a dedicated branch, never directly to `main`.
- Branch names should include a sanitized work/task identifier.
- Every write must include an idempotency key, expected base SHA, request ID, and audit correlation ID.
- File updates must use the observed current blob SHA to detect concurrent changes.
- Pull requests must include purpose, scope, changed files, tests run, risks, and evidence.
- Protect `main`: require pull requests, required CI checks, at least one human approval, and disallow force pushes/deletion.
- Do not grant the AI App bypass permissions.
- Merge and deployment remain human-approved operations.

## 8. Public repository considerations

Public visibility permits unauthenticated reading of public source, but does not grant write permission. Writes still require the GitHub App installation and its scoped permissions.

Before enabling broad public visibility or accepting contributions, scan repository history and current files for credentials, personal data, private endpoints, internal configuration, and sensitive artifacts. Removing a secret from the latest commit does not remove it from Git history; rotate exposed credentials.

## 9. Audit and evidence

For every operation record:

- event ID, timestamp, request ID, trace ID, idempotency key;
- verified subject, tenant, model/client identity;
- repository, branch, operation, target path or PR number;
- policy decision and approval receipt reference;
- installation identity (not token value);
- before/after commit SHA or PR head SHA;
- result, error code, duration, and evidence references.

Audit records must be append-only and must never contain access tokens, private keys, OAuth codes, or secret values.

## 10. Failure and retry semantics

- Fail closed when identity, tenant mapping, policy, installation, or secret manager is unavailable.
- Retry only transient failures and only with the same idempotency key.
- On timeout after a potentially completed write, reconcile GitHub state before retrying.
- Treat 401/403 as authorization failures; do not retry by escalating permissions.
- Treat 409/422 and stale SHA responses as conflicts requiring a fresh read and re-evaluation.
- Rate-limit by verified subject, tenant, installation, and repository.

## 11. Rollout gates

| Gate | Acceptance requirement | Status |
|---|---|---|
| G0 | Contract and threat model reviewed | NOT RUN |
| G1 | GitHub App created with minimum permissions | NOT RUN |
| G2 | App installed only on approved repositories | NOT RUN |
| G3 | IM19 identity, tenant resolver, secret manager and token broker integrated | NOT RUN |
| G4 | Read-only tools pass unit and security tests | NOT RUN |
| G5 | Branch-only write and PR flow pass staging tests | NOT RUN |
| G6 | Cross-tenant, token-leakage, replay, SSRF and policy-bypass tests pass | NOT RUN |
| G7 | Human approval and protected-branch merge acceptance pass | NOT RUN |
| G8 | Production deployment, monitoring, incident response and rollback verified | NOT RUN |

No gate is considered passed from documentation alone. Capture commit-pinned test output, staging request IDs, sanitized logs, and reviewer sign-off.

## 12. Current implementation boundary

This document defines the target contract only. It does not create a GitHub App, install it, provision a secret manager, implement GitHub MCP tools, enable write access, or establish live ChatGPT/Claude/Gemini connectivity.

The existing IM19 outbound MCP router described in `docs/UPSTREAM_MCP_ROUTER.md` is read-only and disabled unless explicitly injected at deployment. Do not route GitHub write operations through that generic read-only router. Implement a dedicated GitHub App adapter and authorization boundary, with tests, before enabling write tools.

## 13. Definition of done

- GitHub App exists and is installed only on approved repositories.
- Private key and installation tokens remain in the server-side secret boundary.
- ChatGPT and other model clients authenticate to IM19 independently.
- Each read/write tool enforces tenant, repository, branch, role, policy, and approval.
- AI writes only to working branches and creates pull requests.
- Protected branch rules and required checks are verified in GitHub.
- Security, contract, integration, and staging tests pass with reproducible evidence.
- Production deployment and operational ownership are documented.
