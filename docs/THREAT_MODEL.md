# IM19 Threat Model (STRIDE-inspired)

| Threat | Trust boundary | Mitigation (current) | Remaining gap |
|---|---|---|---|
| Forged MCP principal | ChatGPT -> MCP | RFC 7662 introspection, issuer/audience/expiry/scopes/sub checks | Provider-specific test and JWT/JWKS alternative |
| Authorization confusion | MCP -> Google | Separate Google OAuth and MCP resource issuer | Full OIDC UX and live end-to-end validation |
| OAuth CSRF/replay / account-link injection | Browser -> callback -> MCP | First-party browser PKCE login, exact issuer-verified subject/client proof, cookie/purpose-bound one-time state; staged credentials require originating MCP subject confirmation | Live issuer acceptance and anti-phishing UX |
| Cross-user provider access | MCP -> vault | Subject+service credential key; atomic pending confirmation matched to same authenticated subject | Multi-tenant staging penetration testing |
| Token disclosure | Database/logs | Fernet encryption, audit pseudonymization, no Google tokens in tool outputs, callback no-store/referrer headers and Uvicorn access logging disabled; isolated offline all-table key rotation with primary-only verification | Managed KMS/HSM, verified backup key lifecycle and ingress log redaction |
| SSRF | Tool -> provider | Fixed allowlisted Google API domains, strict Drive file IDs, escaped search literals | Egress allowlist at network layer |
| Provider API downtime | Provider -> MCP | Bounded HTTP timeout, normalized errors | Circuit breaker, budgeted retries |
| Prompt injection | External data -> model | Drive text flagged untrusted, external content treated as data, no mutation tools | Content filters / provenance |
| Unauthorized mutation | Model -> tools / future adapters | Read-only MCP tools; DB approval tokens bound to exact identity, resource and canonical payload; one-time atomic consume | Trusted human-authenticated portal and write-adapter enforcement are NOT implemented |
| State loss | Infra -> DB | PostgreSQL + Alembic migrations 0001–0005; persisted claim, refresh leases, approvals, idempotency receipts and pseudonymous quota rows; key-rotation instructions include backup retention | Verified backups, PITR, restore drills and secure historical key retention |
| Concurrent refresh / stale credential resurrection | OAuth token backend -> DB | Atomic DB leases, 30-second expiration, CAS ownership and account binding on update, reauthorization invalidates active lease | Refresh load testing / clock skew and timeout modeling |
| Confirmation code phishing | Browser -> user -> ChatGPT | Browser proof before provider consent, random code hashed at rest, one-time/5-minute TTL, bound to subject + service | Human anti-phishing review and real issuer/browser acceptance |
| Key compromise / cryptographic migration | Secrets manager -> encrypted DB | Primary-only production key config; offline MultiFernet rewrap transaction and full decrypt verification | KMS/HSM, secret custody controls, key expiration and rollback drills |
| Rate abuse | Network -> HTTP / MCP tools | PostgreSQL atomic shared quotas for socket-peer and issuer-verified MCP subject, fail-closed on unavailable DB, identity hashed at rest | Reverse proxy/WAF verified end-client IP rate limits, burst controls, load/abuse testing and alerts |
| Approval replay / changed payload | Future trusted portal -> provider write | SHA-256 payload digest, exact user/account/action/resource match, atomic one-time grant and idempotency receipt | Portal identity verification and real mutation integration tests |
| Mutation after provider timeout / crash | Database -> external API | Durable RESERVED receipt blocks implicit re-execution; operator reconciliation required | Provider-side idempotency contracts and operational reconciliation runbooks |
| Forged human approval | Future human portal -> approval DB | No issuance MCP tool; evidence reference and optional four-eyes check in storage API | **No authenticated portal yet**; caller-authenticated approver role verification required |
| Silent readiness failure | Database migrations -> HTTP health | `/ready` requires DB response and revision 0005, `/health` only process liveness | Monitor and alert integration not configured |
| Excessive Google Drive permissions | Google consent -> Drive API | Metadata-only default, separate restricted drive.readonly opt-in, per-service vault | Google OAuth restricted-scope verification and security assessment |
| Google Drive content exfiltration | Drive API -> LLM | 128 KiB hard cap, supported text MIME allowlist, no file writes | DLP/privacy retention controls and user-visible source confirmation |

Controls are not considered production-verified until exercised with deployed
identity provider, secrets manager, real provider APIs and security review.

