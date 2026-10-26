# IM19 0.3.0 — Read-only gateway architecture

## Mission
A single provider-neutral MCP integration gateway, not a substitute for the
official APIs or OAuth consent. This iteration implements the MCP control plane,
an initial Google OAuth token lifecycle, and YouTube Data/Analytics read-only APIs, plus bounded Drive metadata and
explicitly authorized text retrieval.

## Trust zones

```text
ChatGPT (OAuth client)
   |  Bearer token issued by separate MCP authorization server
   v
IM19 Remote MCP (MCP resource server /mcp, issuer + aud + scopes verified)
   |   verified subject only; never user_id from tool args
   +-- MCP tool registry
   +-- first-party browser PKCE login -> same-subject/client introspection
   +-- browser/purpose-bound one-time OAuth state + pending link claim -> PostgreSQL
   |    +-- browser displays confirmation code -> authenticated MCP user confirms
   +-- encrypted provider credential vault -> PostgreSQL
   +-- DB refresh leases with stale-worker ownership checks
   +-- PostgreSQL atomic rate windows (HMAC socket peer and verified subject)
   |    +-- HTTP 429/503 and MCP RATE_LIMITED/SERVICE_UNAVAILABLE
   +-- audit store (pseudonymous subject, action, timestamp, status)
   +-- Google OAuth flow --> Google consent + token + userinfo
   +-- YouTube read-only connector --> official YouTube Data/Analytics APIs
   +-- Drive metadata/text connector --> official Drive v3 APIs (text requires new scope)
```

The OAuth identity provider for ChatGPT-to-IM19 MUST issue tokens with resource
audience equal to IM19_MCP_RESOURCE. Google OAuth is independent and is not the
MCP authorization issuer.

## Modules

- config.py — typed runtime configuration, mandatory security configuration for remote mode.
- registry.py — declared, separate service capabilities; not a connection claim.
- security.py — deny-by-default scoped user/provider/account checks and exact approval policy.
- storage.py — SQLAlchemy tables, Fernet-encrypted provider credentials, encrypted pending confirmations, one-use state and refresh leases.
- google_oauth.py — Google Authorization Code + PKCE, callback stages encrypted tokens, explicit verified-subject confirmation, cross-worker refresh and revocation primitives.
- youtube.py — fixed-host, read-only official Google APIs, sanitized provider error classes.
- drive.py — metadata search, folder listing and bounded opt-in text reads from fixed Drive v3 endpoints.
- server.py — tool discovery, consent URL, verified-user confirmation, status, protected YouTube/Drive tools.
- browser_auth.py — dedicated browser-client Authorization Code + PKCE and exact issuer-verified subject/client matching before Google consent.
- outbound.py — bounded streaming reads, total deadlines and strict JSON parsing.
- database.py — PostgreSQL connection/statement/lock/pool deadlines.
- http_security.py — host/origin validation and protective headers on all routes.
- gateway.py — OAuth bearer introspection for remote HTTP; public health and OAuth callback only; fail-closed database-backed socket-peer rate limiter in production.
- distributed_quota.py — atomic shared PostgreSQL fixed-window quotas with pseudonymous keys and expiry cleanup; MCP tool quotas use only a verified OAuth subject.

## Operational boundaries

- Local stdio mode accepts an optional IM19_DEV_SUBJECT strictly for trusted development.
- Remote mode refuses startup without issuer/resource/introspection secrets and token vault key.
- Remote mode does not honor the stdio development subject.
- OAuth callback is public but state is random, service-bound, time-bound, server-stored
  and atomically single-use. Browser callback stages encrypted credentials but cannot
  connect an account: the same authenticated MCP principal must explicitly redeem a
  short-lived confirmation code. Production additionally requires first-party browser
  proof at the MCP issuer and binds both callbacks to the same Secure HttpOnly cookie.
- Exposing /mcp to ChatGPT requires an independent OAuth provider capable of MCP resource binding.
- No provider writes, email sends, uploads, deletes or repository changes are exposed.

## Deferred architectural controls

Deployment acceptance: real issuer/Google/ChatGPT authentication, representative
load tests, monitored cleanup, ingress/client quotas, backup/key recovery, privacy
retention and incident response. See PRODUCTION_READINESS.md. Webhooks, provider
write adapters, write-approval UI and richer account management are separate future
features and are not advertised as implemented by this read-only gateway.

