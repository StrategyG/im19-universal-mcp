# IM19 — Offline Encryption Key Rotation Runbook

**Status:** implemented and tested as a backend maintenance primitive. This is
not managed KMS integration, not an automatic scheduled rotation service,
and not a production security certification.

## Scope and cryptography

- The primary encryption key remains `IM19_FERNET_KEY`.
- Temporarily supply comma-separated old keys as
  `IM19_FERNET_PREVIOUS_KEYS` **only during offline maintenance**.
- MultiFernet encrypts with the primary key and can decrypt with older keys.
- `im19-rotate-keys` re-encrypts every token in one **database transaction**:
  `oauth_credentials.token_ciphertext`,
  `oauth_states.verifier_ciphertext`, and
  `oauth_pending_links.token_ciphertext`.
- A corrupt/unreadable ciphertext aborts the operation and rolls back the
  transaction. No partial migration is accepted.
- The new primary key is verified against every sensitive row without using
  any fallback. Verification prints only per-table counts.
- Existing OAuth expiration, Google identities, approval IDs, payload hashes,
  and audit records are not modified by the crypto migration.
- The underlying Fernet `rotate` operation preserves original token creation
  timestamps; user-visible OAuth token expiry remains a separate DB field.

## Mandatory operator sequence

**First, prepare a verified, encrypted database backup and a rollback
procedure.** Retain the old key securely for decryption of historical backups
during their retention interval. Never commit, log, paste, or return either key
to ChatGPT.

1. Put the gateway into maintenance mode. Remove it from public routing.
   **Stop every MCP gateway, background worker, scheduled job and token writer.**
   Confirm there are no active application connections or writes in the DB.
   `IM19_OFFLINE_MAINTENANCE=YES` is **an operator declaration only**, not a
   distributed lock or proof that every writer is offline.
2. Use a trusted secrets manager to generate and inject a new Fernet key.
   Keep all currently required previous keys available to the offline process.
3. Supply the maintenance environment:

   ```text
   IM19_DATABASE_URL=postgresql+psycopg://.../im19
   IM19_FERNET_KEY=<new 32-byte Fernet base64 key>
   IM19_FERNET_PREVIOUS_KEYS=<old Fernet key>[,<older Fernet key>...]
   IM19_OFFLINE_MAINTENANCE=YES
   ```

4. Run `im19-rotate-keys` exactly once from an isolated maintenance
   environment. It returns a `ROTATED_AND_VERIFIED` status and row counts.
   Failures must stop rollout; restore only with a verified backup and matching
   key material.
5. Remove `IM19_FERNET_PREVIOUS_KEYS` and
   `IM19_OFFLINE_MAINTENANCE` from runtime environment. Execute
   `im19-verify-primary-key` with only the new primary key.
6. Restart the gateway and require HTTP `/ready` success. Verify that
   a previously authorized test account can still perform its read-only
   provider actions. Re-check Google token refresh with staging credentials.
7. Verify system logs, external monitoring and token retention, then
   reintroduce traffic only after staging acceptance. Retire old secrets from
   active secret stores once no running process or backup restore workflow
   requires them.

## Hard safety boundaries

- The **production gateway rejects legacy fallback keys entirely**.
  Temporary legacy keys are for isolated offline migration, not online rollover.
- The command cannot prove all writers were stopped. Require independent
  operator confirmation and infrastructure controls.
- Do NOT run concurrent copies of the rotation command.
- Do NOT delete old keys while encrypted historical backups still need them.
- Do NOT store both new and old keys indefinitely in a shared environment.
- Do NOT run the maintenance command from an AI or public MCP tool: it is a
  trusted operator action.
- Key rotation does **not** constitute KMS/HSM integration, hardware-backed
  key custody, separation of operator roles or tested disaster recovery.

## Acceptance tests

Automated tests cover multi-key decrypt, primary-only encryption, all three
ciphertext columns, no mutation without an offline-ack flag, full transaction
rollback on corrupted ciphertext, rejection of duplicate/invalid keys, absence
of keys in CLI output, and primary-only decryption after migration.
The disposable PostgreSQL 16 CI job additionally exercises rotation against
a real database, plus a separate `pg_dump`/`pg_restore` smoke test of a
synthetic encrypted credential and schema revision. No production credentials
or sensitive live content are used. Real production backup, offsite retention,
and disaster-recovery acceptance remain mandatory.

Reference: https://cryptography.io/en/latest/fernet/#multifernet
