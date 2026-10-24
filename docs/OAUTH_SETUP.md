# Google OAuth — hardened linking workflow (staging)

This is a **two-step account-linking flow**. The Google callback does not
activate a connection. A confirmation code must be submitted by the
**same authenticated MCP principal** that initiated authorization.

## Prerequisites

1. Create a Google Cloud OAuth **Web application** with an exact callback
   `https://YOUR_HOST/oauth/google/callback`.
2. Enable the relevant official YouTube/Analytics or Drive APIs. Review all
   required Google OAuth scope verification and restricted-scope requirements.
3. Configure `IM19_GOOGLE_CLIENT_ID`, `IM19_GOOGLE_CLIENT_SECRET`,
   `IM19_GOOGLE_REDIRECT_URI`, `IM19_FERNET_KEY` and `IM19_DATABASE_URL`
   using a secrets manager. They must never be supplied as MCP arguments.
4. Deploy an independently authenticated MCP resource server; its issuer,
   subject, audience and scopes are validated separately from Google OAuth.
5. Apply migrations **through 0003_refresh_leases** before starting the gateway:
   `alembic upgrade head` (or `docker compose run --rm gateway alembic upgrade head`).

## Authorization (human confirmation required)

1. A verified MCP user invokes
   `request_service_authorization(service="youtube")` or
   `request_service_authorization(service="drive")`.
2. IM19 persists a 10-minute PKCE S256 transaction with a cryptographic
   state bound to that MCP subject and service. The tool returns the official
   Google consent URL.
3. The user opens the Google URL in their browser, authenticates at Google,
   reviews requested scopes, and accepts or denies consent.
4. Google redirects to the IM19 callback. Backend atomically consumes the
   state, exchanges the code, verifies granted scopes, fetches Google's
   stable userinfo `sub`, and **only stages encrypted tokens**.
5. The callback shows a **one-time 5-minute confirmation code**, not a Google
   access/refresh token. It is returned with no-store/no-referrer/anti-framing
   headers. Do not share the code with anyone else.
6. The user copies the code into:
   `confirm_service_authorization(service="youtube", confirmation_code="...")`
   (or `drive`). The MCP server verifies the originating authenticated subject
   and service, and atomically consumes the code before persisting the encrypted
   provider credential. Another user's MCP session cannot claim that code.
7. The user calls `get_connection_status(service="youtube")` to inspect status
   and granted scopes. Live provider reads should then be tested.

**Important:** the confirmation-code fallback mitigates silent account-linking
CSRF; it does **not** provide full browser-session / OIDC session binding.
A production first-party account portal with verified sessions remains a
release gate, and users can still be socially engineered into sharing codes.

## Incremental provider authorization

- YouTube: `youtube.readonly` default; `include_analytics=True` explicitly adds
  YouTube Analytics.
- Drive: `drive.metadata.readonly` default; `include_content=True` explicitly
  adds `drive.readonly`. Both Drive scopes are Google **restricted scopes**.
- Denied scopes produce `SCOPE_REQUIRED`; authorization is never silently
  elevated or bypassed.
- Each provider service has its own credential entry.
- Refresh uses a 30-second DB lease and stale-worker compare-and-set update,
  preventing concurrent refresh and revoked-token resurrection. A concurrent
  request may receive `REFRESH_IN_PROGRESS` and should retry manually later.

## Incident handling and callback hygiene

- Do not log `/oauth/google/callback` query parameters: Google's authorization
  `code` and `state` are URL parameters. The bundled Uvicorn entrypoint
  disables HTTP access logs; **configure ingress/CDN logs separately**.
- Never log or expose refresh tokens, Google access tokens, OAuth code
  verifiers or encrypted vault keys.
- Use `alembic upgrade head` for upgrades; do not run `metadata.create_all`
  as a production substitute.
- Rotating Fernet keys without decrypt-and-reencrypt migration makes existing
  credentials unreadable; use managed rotation and backup procedures.
- Revocation/disconnect is not exposed as an AI action. An independently
  authenticated and approved account portal remains to be implemented.

**Live verification remains outstanding:** no Google sandbox or real account
login and no end-to-end ChatGPT session were used in automated mock tests.

Official: https://developers.google.com/identity/protocols/oauth2/web-server
