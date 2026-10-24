# Security checklist

- [x] Provider OAuth separate from MCP client OAuth.
- [x] OAuth PKCE and one-time state with TTL.
- [x] Google API URL allowlist and read-only operations.
- [x] Token ciphertext at rest (Fernet).
- [x] Authenticated user subject never comes from tool args.
- [x] MCP access token issuer, audience, expiry, scope checked via introspection.
- [x] No provider mutation tools exposed.
- [x] Multi-user store keys and sanitized audit event schema.
- [ ] Rotate encryption keys via managed KMS.
- [ ] Verify production authorization server + end-to-end ChatGPT OAuth.
- [ ] Human approval persistence, single-use semantics and idempotency.
- [ ] Distributed rate limiting and refresh locking.
- [ ] Complete backup, DR and pen-test exercises.
- [ ] Verify YouTube and Google OAuth with real test accounts.
- [ ] Production audit retention and redaction review.
