# IM19 Drive write: opt-in, approval-gated foundation

This is **one concrete write operation** added to the existing read-only
gateway: `drive_create_folder` creates an empty folder in the root of the
connected Google Drive account. Other provider write tools, deletion, upload,
publication remain **unimplemented**. An initial OAuth-authenticated review page
is implemented for this one operation, but has not passed live production acceptance.

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
3. A one-time approval issued by the trusted IM19 backend **after the user
   logs in through the IM19 identity provider and presses Setujui** on a secure
   review page. The model cannot issue an approval itself.
4. A unique idempotency key (8-128 permitted characters).
5. A successful provider POST followed by GET metadata verification.

**No MCP tool can issue an approval.** The gateway now exposes a restricted
browser review flow. The backend holds the ledger token; it is never returned
to an AI client in the normal remote workflow. Keep writes disabled until the
new interface is independently security-tested and Google live acceptance passes.
The local-stdio test fixture retains the legacy token-only path.

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

## Browser approval sequence

1. Set `IM19_ENABLE_APPROVED_DRIVE_WRITES=true` in a **reviewed staging**
   configuration only, migrate database to `0006_write_approvals`, and
   register `https://MCP_HOST/approve/write/callback` as an exact redirect
   URI for the first-party `im19-browser` OAuth client.
2. The model calls `drive_create_folder(name="...")` without an approval token.
3. IM19 checks the verified MCP subject and stored `drive.file` scope; if
   missing, call `request_service_authorization(service="drive", include_write=true)`.
4. IM19 returns `APPROVAL_REQUIRED` with `approval_url` and
   `approval_request_id`. The link itself **cannot** approve anything.
5. The user opens the approval URL in Chrome/Android or another browser,
   completes IM19 OAuth login, reviews the exact folder name and location,
   and presses **Setujui** or **Tolak**.
6. The approval token is created by trusted server code and encrypted in
   PostgreSQL; it is never supplied to the model.
7. The AI calls `drive_create_folder` again with the identical name and the
   returned `approval_request_id`; the server atomically claims approval,
   reserves the idempotency key, executes the write once and verifies Google.
8. The AI can call `drive_get_write_approval_status(approval_request_id="...")`
   to check whether human approval is still pending, approved, denied or used
   without executing the write. `APPROVED` does not mean a folder was created.
9. Denied/expired/consumed or mismatched approvals fail closed.
10. The operator-only `im19-purge-expired` removes expired approval request
    records and encrypted temporary browser state.

The approval is bound to verified subject, Google account, exact payload,
OAuth browser identity, CSRF nonce and a short expiry. Opening the review
URL does **not** itself grant write permission.

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
- Live Google Drive write, production portal security acceptance, ChatGPT integration,
  provider scope verification, and production deployment are **NOT_VERIFIED**.
- This change does not remove earlier read-only tools.
- Browser Worker PR #14 is separate and does not provide write access.
- Keep the backend-held approval token out of MCP/AI output, browser HTML,
  public logs, chat messages, and ticket systems.
- Existing Codespaces identities need a controlled update to add the new exact
  browser OAuth callback; running an old realm will reject the new callback.
