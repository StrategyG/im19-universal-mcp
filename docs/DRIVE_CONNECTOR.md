# IM19 Google Drive Connector — Phase 4 (read-only)

## Status

**Implemented and covered with mock API tests. Not connected to a real Google
account; not approved for public restricted-scope deployment.**

The same MCP server supports YouTube and Drive with **separate OAuth
transactions and per-service encrypted credential entries**.

## Default consent and tools

The default Drive consent requests `openid` and
`https://www.googleapis.com/auth/drive.metadata.readonly` only.

| MCP tool | Purpose | OAuth scope |
|---|---|---|
| `drive_search_files(term, page_size, page_token)` | Search accessible file names, page 1–100 | `drive.metadata.readonly` |
| `drive_list_folder(folder_id, page_size, page_token)` | Direct children, no recursion | `drive.metadata.readonly` |
| `drive_get_metadata(file_id)` | Bounded basic file metadata | `drive.metadata.readonly` |
| `drive_read_file(file_id)` | Text/Google Docs only, max 128 KiB | **`drive.readonly`** |

To enable file-content reading for a connected account, explicitly invoke:

`request_service_authorization(service="drive", include_content=True)`.

Google may require restricted-scope OAuth verification and independent
security assessment for applications that store or transmit such data. A
metadata-only grant cannot be used to read file contents, even though a
file appears in search results.

The content tool is strictly bounded to 128 KiB and only supports UTF-8
`text/plain`, `text/csv`, `text/markdown`, `application/json`,
`application/xml`, and Google Docs exported as plain text. It **does not**
read PDF, EPUB, Sheets, images, or other binary files. Unsupported
types return `UNSUPPORTED_MIME_TYPE`; large bodies return
`RESPONSE_SIZE_LIMIT_EXCEEDED`. Text is explicitly marked untrusted.

## Safety controls

- MCP resource bearer token is validated independently of Google OAuth.
- Subjects come from verified MCP token context, not tool arguments.
- Drive OAuth state is bound to the Drive service in persistent storage.
- OAuth state is single-use and atomically deleted on consumption.
- Drive vault entry is separate from YouTube vault entry.
- Fixed official Google Drive API HTTPS origin, sanitized resource ID,
  escaped Drive search literal, bounded pagination, and request timeout.
- Provider errors normalized without returning raw credentials or API error bodies.
- No mutations, uploads, deletions, permission changes, sharing or posting.
- Provider API data is untrusted, not instructions to the agent.

## Tests and limitations

Automated tests use `httpx.MockTransport` and in-process MCP client,
never real Google credentials.

Production gates remaining: Google OAuth app verification for restricted
scopes, consent-screen review, encryption key lifecycle, independent OAuth
issuer and ChatGPT login tests, user-session binding, distributed request
budgets, backup/restore, and compliance review.

Official sources:
- https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/export
