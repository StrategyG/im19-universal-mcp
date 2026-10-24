# IM19 — Architecture Design Document (Phase 1–3)

## Mission
A single provider-neutral MCP integration gateway, not a substitute for the
official APIs or OAuth consent. This iteration implements the MCP control plane,
an initial Google OAuth token lifecycle, and YouTube Data/Analytics read-only APIs.

## Trust zones

```text
ChatGPT (OAuth client)
   |  Bearer token issued by separate MCP authorization server
   v
IM19 Remote MCP (MCP resource server /mcp, issuer + aud + scopes verified)
   |   verified subject only; never user_id from tool args
   +-- MCP tool registry
   +-- OAuth state/credential store -- encrypted Fernet -> PostgreSQL
   +-- audit store (pseudonymous subject, action, timestamp, status)
   +-- Google OAuth flow --> Google consent + token + userinfo
   +-- YouTube read-only connector --> official YouTube Data/Analytics APIs
```

The OAuth identity provider for ChatGPT-to-IM19 MUST issue tokens with resource
audience equal to IM19_MCP_RESOURCE. Google OAuth is independent and is not the
MCP authorization issuer.

## Modules

- config.py — typed runtime configuration, mandatory security configuration for remote mode.
- registry.py — declared, separate service capabilities; not a connection claim.
- security.py — deny-by-default scoped user/provider/account checks and exact approval policy.
- storage.py — SQLAlchemy tables, Fernet-encrypted provider credential documents, one-use state.
- google_oauth.py — Google Authorization Code + PKCE, callback exchange, refresh and revocation.
- youtube.py — fixed-host, read-only official Google APIs, sanitized provider error classes.
- server.py — tool discovery, service authorization URL, status, protected YouTube tools.
- gateway.py — OAuth bearer introspection for remote HTTP; public health and OAuth callback only.

## Operational boundaries

- Local stdio mode accepts an optional IM19_DEV_SUBJECT strictly for trusted development.
- Remote mode refuses startup without issuer/resource/introspection secrets and token vault key.
- Remote mode does not honor the stdio development subject.
- OAuth callback is public but state is random, time-bound, server-stored and single-use.
- Exposing /mcp to ChatGPT requires an independent OAuth provider capable of MCP resource binding.
- No provider writes, email sends, uploads, deletes or repository changes are exposed.

## Deferred architectural controls

Before production: Redis/DB coordination of refresh jobs; key rotation; full
provider webhook integration; post-disconnect revocation status tracking;
OIDC session binding and account-selection UX; deployment monitoring and
alerts; retention/privacy policy; managed KMS/HSM; stronger app gateway rate
limits; multi-tenant penetration tests; evidence-backed failover drills.
