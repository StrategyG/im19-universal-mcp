# IM19 Threat Model (STRIDE-inspired)

| Threat | Trust boundary | Mitigation (current) | Remaining gap |
|---|---|---|---|
| Forged MCP principal | ChatGPT -> MCP | RFC 7662 introspection, issuer/audience/expiry/scopes/sub checks | Provider-specific test and JWT/JWKS alternative |
| Authorization confusion | MCP -> Google | Separate Google OAuth and MCP resource issuer | Full OIDC UX and live end-to-end validation |
| OAuth CSRF/replay / account-link injection | Browser -> callback -> MCP | Cryptographic state and PKCE; callback stages encrypted credentials; independent one-time code must be redeemed by the originating verified MCP subject | Authenticated browser session binding and anti-phishing UX |
| Cross-user provider access | MCP -> vault | Subject+service credential key; atomic pending confirmation matched to same authenticated subject | Multi-tenant staging penetration testing |
| Token disclosure | Database/logs | Fernet encryption, audit pseudonymization, no Google tokens in tool outputs, callback no-store/referrer headers and Uvicorn access logging disabled | Managed KMS, ingress query-string log redaction and rotation |
| SSRF | Tool -> provider | Fixed allowlisted Google API domains, strict Drive file IDs, escaped search literals | Egress allowlist at network layer |
| Provider API downtime | Provider -> MCP | Bounded HTTP timeout, normalized errors | Circuit breaker, budgeted retries |
| Prompt injection | External data -> model | Drive text flagged untrusted, external content treated as data, no mutation tools | Content filters / provenance |
| Unauthorized mutation | Model -> tools | All connector tools are read-only; approval policy separately available | Durable one-time approval issuance/consumption |
| State loss | Infra -> DB | PostgreSQL + Alembic migrations 0001–0003; persisted claim and refresh leases | Backups, PITR, disaster recovery tests |
| Concurrent refresh / stale credential resurrection | OAuth token backend -> DB | Atomic DB leases, 30-second expiration, CAS ownership and account binding on update, reauthorization invalidates active lease | Refresh load testing / clock skew and timeout modeling |
| Confirmation code phishing | Browser -> user -> ChatGPT | Random code hashed at rest, one-time/5-minute TTL, bound to verified subject + service | Authenticated browser portal / OIDC session binding and human anti-phishing warnings |
| Rate abuse | Network -> HTTP | SDK body limits; provider calls capped | Distributed user/IP rate limiting |
| Excessive Google Drive permissions | Google consent -> Drive API | Metadata-only default, separate restricted drive.readonly opt-in, per-service vault | Google OAuth restricted-scope verification and security assessment |
| Google Drive content exfiltration | Drive API -> LLM | 128 KiB hard cap, supported text MIME allowlist, no file writes | DLP/privacy retention controls and user-visible source confirmation |

Controls are not considered production-verified until exercised with deployed
identity provider, secrets manager, real provider APIs and security review.
