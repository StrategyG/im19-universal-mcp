# P0 Identity Compatibility — IM19 Universal MCP

**Canonical reference:** P0 contract draft in Universal-Generative-AI, docs/IDENTITY-COMPATIBILITY-P0.md.  
**Status:** Proposed mapping; live local-issuer interoperability NOT VERIFIED.

## IM19 identity boundary

IM19 has three distinct identity dimensions and they MUST remain separate:

1. Gateway principal: authenticated MCP caller, represented by issuer-scoped sub and validated for the IM19 resource audience.
2. Tenant context: canonical UUID tenant_id asserted by the trusted local issuer and carried through server-side policy context. It must not be inferred from provider metadata.
3. Provider account: linked external account identified by (provider, account_id) and bound to the authenticated subject. Provider OAuth access/refresh tokens are stored encrypted and never become gateway bearer credentials.

## P0 requirements

- Configure a dedicated IM19 gateway issuer/resource audience. A UGAI or Notebook audience token must not be accepted by IM19 unless explicitly exchanged for the IM19 audience.
- Preserve sub from the verified gateway token; never accept subject, tenant, provider or account identity from request body or forwarded headers.
- Bind every linked provider account to the authenticated subject and tenant in persistent records.
- Enforce provider scopes and account binding on every operation.
- Mutating operations remain disabled by default and require approval bound to the exact subject, provider, account, resource, action, payload hash and expiry.
- Reject Google/provider access tokens when presented as IM19 gateway tokens.
- Keep OAuth client identity, human subject and provider account ID distinct.
- Keep local deployment possible with a local OIDC issuer/JWKS, local PostgreSQL and local secret/key management. Google provider access remains an optional separately consented integration.

## P0 tests to add

- UGAI/Notebook audience token rejected by IM19 gateway.
- Provider access token rejected as gateway bearer token.
- Same subject with a different provider account cannot cross-read or mutate another account.
- Same provider account cannot be used by a different subject/tenant.
- Missing provider scope returns denial.
- Mutation without exact-action approval returns approval-required/deny.
- Expired, wrong-subject, wrong-tenant or payload-mismatched approval is rejected.
- Local issuer discovery/JWKS and IM19 audience token validated in an isolated integration test.

## Current evidence and limitations

IM19 source already models subject, provider, account_id and scopes separately and its README describes a dedicated gateway OAuth relationship distinct from Google OAuth. This is a boundary to preserve. Source inspection does not prove tenant propagation through every persistence/query path or a live shared local issuer. Verify those before declaring P0 complete.
