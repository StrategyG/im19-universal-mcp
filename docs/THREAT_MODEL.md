# IM19 Threat Model (STRIDE-inspired)

| Threat | Trust boundary | Mitigation (current) | Remaining gap |
|---|---|---|---|
| Forged MCP principal | ChatGPT -> MCP | RFC 7662 introspection, issuer/audience/expiry/scopes/sub checks | Provider-specific test and JWT/JWKS alternative |
| Authorization confusion | MCP -> Google | Separate Google OAuth and MCP resource issuer | Full OIDC UX and live end-to-end validation |
| OAuth CSRF/replay | Browser -> callback | Cryptographic state, PKCE S256, DB one-use state, TTL | Authenticated browser session binding UX |
| Cross-user provider access | MCP -> vault | Subject+service credential key, no subject model argument | Concurrency and multi-tenant audit |
| Token disclosure | Database/logs | Fernet encryption, audit pseudonymization, no tokens in tool outputs | Managed KMS, rotation and secret scanning |
| SSRF | Tool -> provider | Fixed allowlisted Google API domains, no user-supplied URLs | Egress allowlist at network layer |
| Provider API downtime | Provider -> MCP | Bounded HTTP timeout, normalized errors | Circuit breaker, budgeted retries |
| Prompt injection | External data -> model | External content treated as data, no mutation tools | Content filters / provenance |
| Unauthorized mutation | Model -> tools | All connector tools are read-only; approval policy separately available | Durable one-time approval issuance/consumption |
| State loss | Infra -> DB | PostgreSQL + Alembic paths | Backups, PITR, disaster recovery tests |
| Rate abuse | Network -> HTTP | SDK body limits; provider calls capped | Distributed user/IP rate limiting |

Controls are not considered production-verified until exercised with deployed
identity provider, secrets manager, real provider APIs and security review.
