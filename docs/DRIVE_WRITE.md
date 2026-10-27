# IM19 Drive write: opt-in, approval-gated foundation

This is **one concrete write operation** added to the existing read-only
gateway: `drive_create_folder` creates an empty folder in the root of the
connected Google Drive account. Other provider write tools, deletion, upload,
publication, and a trusted human approval portal remain **unimplemented**.

## Controls

Write is **OFF by default**. The operator must explicitly configure
`IM19_ENABLE_APPROVED_DRIVE_WRITES=true` in a protected, reviewed deployment
configuration before the tool may execute. Codespaces' generated stack keeps
this flag disabled unless separately changed and redeployed.

The tool requires:
1. A verified MCP user identity.
2. A Google Drive connection with explicitly consented
   `https://www.googleapis.com/auth/drive.file` scope. The normal metadata
   grant **does not** authorize creation.
3. A one-time approval issued outside MCP by trusted, independently
   authenticated backend/human review using `ApprovalLedger.issue()`, bound to
   the exact user, account, action, resource and payload.
4. A unique idempotency key (8-128 permitted characters).
5. A successful provider POST followed by GET metadata verification.

**No MCP tool can issue an approval.** The existing ledger deliberately has no
user-facing portal or trusted issuance endpoint. Do not enable real write
operations until those workflows have been reviewed and deployed. For testing,
unit tests call `ApprovalLedger.issue` directly with an operator fixture.

## Exact approval binding

Approval issue arguments:

- `subject`: MCP verified subject.
- `provider`: `drive`.
- `account_id`: connected Google account ID from the encrypted vault.
- `action`: `drive.create_folder`.
- `resource_id`: `root`.
- `payload_hash`: `payload_sha256({"name": "<folder name>", "mimeType": "application/vnd.google-apps.folder"})`.
- `approver`: separately authenticated human/operator.
- `evidence_reference`: authenticated review/audit record.
- `ttl_seconds`: 30-600.

The current prototype supplies this short-lived one-time approval token in the
MCP tool call. **This is not the desired production human-approval UX**: the
model and client can see the token. Replace with backend-held approval
correlation and a trusted account dashboard before accepting real user writes.

This tool never creates or updates a Google OAuth credential; the user must
explicitly request new Drive consent using
`request_service_authorization(service="drive", include_write=true)` and
complete browser login and one-time confirmation first. Google OAuth is
disabled by default in the Codespaces test stack.

## Provider and reconciliation

The API uses the fixed Google Drive files endpoint for a root-folder POST,
then GETs the created file ID to verify name and MIME type. The operation
never follows provider redirects or uses an arbitrary URL. Google Drive
`files.create` is not assumed idempotent.

If the POST times out, verification fails or the process crashes, the
reservation stays `RESERVED` and the MCP response is `UNKNOWN_OUTCOME`.
Do not reuse its idempotency key or retry automatically. A trusted operator
must check Google Drive and reconcile before any new attempt.

A verified success commits `SUCCEEDED` into the existing append-only audit
and mutation receipt workflow. If finalization fails, response is
`PENDING_VERIFICATION` rather than an ungrounded success claim.

## Acceptance boundaries

- Mocked API/OAuth and database tests demonstrate code behavior only.
- Live Google Drive write, approval user interface, ChatGPT integration,
  provider scope verification, and production deployment are **NOT_VERIFIED**.
- This change does not remove earlier read-only tools.
- Browser Worker PR #14 is separate and does not provide write access.
- Do not expose approval tokens in public logs, chat messages or ticket systems.
