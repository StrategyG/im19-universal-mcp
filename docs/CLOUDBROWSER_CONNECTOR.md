# Cloudbrowser A2A read-only MCP connector

This integration connects the existing **StrategyG/Cloudbrowser** A2A 1.0
orchestrator to **StrategyG/im19-universal-mcp**. It does **not** run browser
automation inside IM19 and does not imply a live Cloudbrowser connection.

## Trust boundary

```text
Authenticated ChatGPT/IM19 principal (issuer, audience, im19:read)
     -> IM19 verified subject allowlist
     -> shared quota + pseudonymous audit
     -> fixed administrator-configured Cloudbrowser A2A origin
     -> owner A2A bearer key (server environment only)
     -> GET agent card / JSON-RPC GetTask or ListTasks
```

Cloudbrowser A2A currently uses **one owner-wide bearer credential**, rather
than tenant-isolated per-user tokens. Consequently, configure
`IM19_CLOUDBROWSER_ALLOWED_SUBJECTS` ONLY for the trusted owner subject(s).
Unlisted principals receive DENY and cannot inspect the owner's tasks.

## Configure

On Cloudbrowser, provision external A2A following its `docs/A2A.md`
(`configure-a2a`). Keep the key in a secrets manager on the IM19 host.
Set these IM19 runtime variables:

```dotenv
IM19_CLOUDBROWSER_URL=https://cloudbrowser.example.com
IM19_CLOUDBROWSER_API_KEY=<owner-key-at-least-32-chars>
IM19_CLOUDBROWSER_ALLOWED_SUBJECTS=<exact-MCP-issuer-subject>
```

Use the **base origin** without `/a2a/rpc`; HTTPS is required except for
explicit HTTP loopback (`127.0.0.1`, `localhost`, `::1`).
The URL must have no query string, credentials, fragment, or extra path.
No browser-side password, Telegram token, or Google credential is used.

The resulting tools are:

| Tool | Behavior |
| --- | --- |
| `cloudbrowser_get_agent_card` | Bounded, sanitized discovery metadata |
| `cloudbrowser_get_task(task_id)` | Read A2A task status for a validated ID |
| `cloudbrowser_list_tasks(page_size, page_token)` | List max 20 tasks per page; no artifact bodies |
| `cloudbrowser_get_task_summary(task_id)` | Stable A2A state, requires_owner_input, is_terminal, workflow_reviewed, completed_and_reviewed, artifact_count |
| `cloudbrowser_list_task_summaries(page_size, page_token)` | Safe task-state list with no raw messages or artifact links |
| `cloudbrowser_get_readiness` | Database, worker, Telegram operational checks from /readyz (HTTP 503 means not ready) |

All results are untrusted and bounded. Requests have short timeouts; redirects
are disabled; failed auth, malformed responses, and upstream errors fail closed.
Connection metadata indicates *configured*, not live verified. Test live access
using the tool and confirm its `status=OK` response.

## Deliberate limitations

- No remote `SendMessage`, `CancelTask`, resume, browser clicks, or writes.
- No dynamic URLs, agent IDs, or model-supplied bearer credentials.
- No downloading private artifacts via MCP; returned artifact references are
  not automatically fetched.
- Neither A2A submission nor Cloudbrowser's existing owner-approval ledger is
  equivalent to the separate IM19 write-approval portal.

A future write-capable integration requires a subject-bound, once-only operator
approval with payload hash, idempotency and reconciliation of uncertain effects.
Keep the Cloudbrowser dashboard/Telegram owner-approval flow for execution.

## Test

```bash
python -m pytest -q tests/test_cloudbrowser_mcp.py tests/test_mcp.py
python -m ruff check src/im19_mcp/cloudbrowser.py tests/test_cloudbrowser_mcp.py
```

This is component validation, **not** live deployment acceptance. Verify A2A
credentials, ACL, outbound TLS/egress policy, logs and operational controls in
the deployed environment before enabling.


## Precise workflow reporting

Use `cloudbrowser_get_task_summary` for agent decisions. The tool checks
that the upstream GetTask ID matches the requested ID and recognizes
`TASK_STATE_SUBMITTED`, `TASK_STATE_WORKING`, `TASK_STATE_INPUT_REQUIRED`,
`TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`, and
`TASK_STATE_CANCELED`. A task marked `COMPLETED` is **not** a reviewed
workflow unless Cloudbrowser's `metadata.workflowReviewed` is exactly true.
`INPUT_REQUIRED` means seek owner intervention; never automatically repeat
a remote mutation. Artifact references are counted but are not fetched by this
tool. Agent-card protocol and RPC URL must match exactly the pinned
`IM19_CLOUDBROWSER_URL` and A2A JSON-RPC 1.0.

CI checks a pinned Cloudbrowser source revision
(`4d65dcd07e984591b16238fb168675d22e9326a3`) using its real
ASGI application and an in-memory HTTP transport. This proves a wire contract,
not running deployment/third-party service readiness. Update the pinned SHA
and rerun the contract check whenever Cloudbrowser's A2A API changes.



## Additional operational reliability controls

- **Readiness:** `cloudbrowser_get_readiness` reads the existing Cloudbrowser
  `GET /readyz` contract, treating HTTP 503 with valid JSON as a diagnostic
  (not a connection exception). The result exposes only fixed boolean checks
  `database`, `worker`, and `telegram`; this is *not* an end-to-end model,
  browser, or render acceptance test.
- **Safe task list:** prefer `cloudbrowser_list_task_summaries` for monitoring.
  It redacts arbitrary status prose, metadata and artifact download URLs.
  `artifact_count=null` means the list RPC omitted artifacts; it does **not**
  mean there are zero files.
- **Audit-before-read:** admission writes `START` to the existing audit ledger
  before contacting Cloudbrowser and `SUCCESS`/`ERROR` after completion.
  If the ledger cannot be written, the upstream request is blocked.
- **Protocol allowlist:** the A2A client refuses any JSON-RPC method except
  `GetTask` and `ListTasks`; it does not provide workflow mutation or
  arbitrary method dispatch.
- **Response safety:** JSON content types and bounded responses are required.
  Remote task and pagination fields must match known types before building
  state summaries.

## Deployment acceptance

A GitHub Actions contract test against a pinned Cloudbrowser source commit
cannot establish production connectivity. Configure real credentials in the
deployment secret manager, prove HTTPS/TLS reachability, read a real task,
verify denied cross-user access, verify audit records, and reconcile
approvals with Cloudbrowser's own independent owner-approval mechanism.


## Deployment preflight

Configure IM19 server secrets (including the exact authorized subject) and
Cloudbrowser's A2A owner credentials in the secret manager, then run:

```bash
im19-cloudbrowser-doctor --mode remote
im19-cloudbrowser-doctor --mode remote --live
```

The first command makes **no network calls** and cannot pass without live
verification. The second calls only the pinned Cloudbrowser agent card,
`/readyz`, and a **one-task read-only ListTasks** probe; it discards
task content and prints only boolean checks and a fixed status code.
No action submission, retry of mutations, artifact downloads, or secret output.

A passing check proves temporary read-only connectivity and operational
readiness, **not** full production acceptance. Remaining gates include live
identity isolation, audit/quotas, recovery drills and deployment approval.
Production remote reads refuse to run without an injected audit vault
and distributed quota; production stdio cannot use `IM19_DEV_SUBJECT`.
