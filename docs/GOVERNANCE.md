# IM19 — Persistent Human Approval & Idempotency (Phase 6 foundation)

**Status: backend security primitive implemented and mock/integration tested.**
An initial trusted OAuth browser approval page exists for Drive folder
creation, but production security acceptance is pending. No general account
administration portal exists. An opt-in `drive_create_folder` tool is implemented as a restricted proof of
integration, but it is disabled by default. **Do not treat this as production-ready approval UX.**

## Boundary and decision contract

```text
External trusted human-authenticated portal (NOT IMPLEMENTED)
    -> Verify explicit human act + protected session + approved payload
    -> ApprovalLedger.issue(subject, provider, account, action, resource, hash,
                            verified_approver, evidence_reference)
    -> Store random approval token hashed in PostgreSQL, 5-minute default expiry

Provider-write orchestration (exposed only for explicitly enabled Drive folder creation)
    -> payload_sha256(canonical JSON)
    -> reserve(token, same subject/provider/account/action/resource/payload,
               idempotency key)
    -> Atomic DELETE RETURNING of one-use approval plus UNIQUE mutation receipt
    -> only then invoke official external API, once
    -> finalize known outcome SUCCEEDED or FAILED_RECONCILIATION_REQUIRED
```

The default MCP configuration remains **read-only**. A restricted Drive root
folder write tool is disabled by default and consumes externally issued grants.
AI-generated text, model confirmation or a guessed approval code cannot issue
any approval. The normal remote flow keeps the approval grant encrypted on the server;
the legacy tool-token path is restricted to trusted stdio development tests.
The new browser review still requires security and real-provider acceptance.

### Properties enforced by the database

- Approvals expire by default after 300 seconds (valid range 30–600).
- Cryptographically random tokens are saved **only as SHA-256 hashes**.
- The grant binds subject, provider, external account, action, resource identifier,
  and SHA-256 of canonical JSON payload.
- Caller of `issue` supplies independently authenticated `approver` and an
  evidence reference. The ledger cannot authenticate humans by itself; such a
  portal is a mandatory deployment prerequisite.
- `four_eyes=True` rejects self-approval. The trusted portal must separately
  enforce approver role and scope; distinct names alone do not prove authority.
- `reserve` atomically consumes approval and reserves the idempotency key, with
  durable database uniqueness protecting against concurrent duplicate writes.
- Matching idempotency keys are **never retried**, even if a previous external
  call failed. A crash while reserved leaves an *uncertain* operation requiring
  manual provider reconciliation. We do **not** promise exactly-once delivery
  across databases and external APIs.
- `finalize` accepts only known result classifications and can transition each
  reservation once. Results are metadata only; no provider secrets are logged.
- `get_receipt` is restricted to the original subject.
- Expired approval grants are purged via trusted `im19-purge-expired`; durable
  mutation receipts must be retained according to a future auditable policy.
- Governance audit events are inserted **in the same DB transaction** as
  successful grant issuance, reservation and finalization. The actor's identity
  must be corroborated by the future portal audit trail.

## Integration rules for future connectors

1. Implement a **separate trusted human-authenticated web portal** to issue
   approvals. Never expose `ApprovalLedger.issue` to an MCP tool or prompt.
2. Verify that the authenticated requester matches the grant's `subject`.
3. Validate the external account and exact resource before executing.
4. Run `reserve` **before** the external mutation. Abort on `ApprovalDenied`,
   `DuplicateExecution` or any DB error. Never bypass on infrastructure failure.
5. Do not retry uncertain provider writes automatically. Query provider status
   using the idempotency key or reconcile manually before deciding further action.
6. Never let external content (RAG results, comments, files, provider responses)
   override approval policy or supply credentials or authorization.
7. Execute an integration/staging load test with PostgreSQL and the target API
   before turning on any mutation feature.

## Acceptance and non-goals

Mock/SQLite tests prove internal one-use/cross-user/idempotency semantics.
GitHub Actions additionally provisions a **disposable PostgreSQL 16 service**,
runs all Alembic migrations, and tests concurrent reservations and unique
idempotency enforcement using real database transactions. This is not a
production workload test and does not validate any external provider mutation.
Live acceptance of browser-bound OAuth, operational key custody/rotation, ingress quotas,
monitoring, disaster recovery, and live ChatGPT login remain unresolved.
External writes are disabled by default. Live provider writes have not been tested.

