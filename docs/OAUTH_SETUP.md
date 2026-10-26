# Google linking with authenticated browser proof

Provider Google OAuth and MCP user authentication use separate credentials.
Production uses the following sequence; a link ticket is never proof of login.

1. A verified MCP subject calls request_service_authorization for YouTube or
   Drive. Elevated analytics/content scopes require explicit opt-in.
2. The tool returns a one-time /connect/google ticket valid for ten minutes.
   Opening it sets a __Host-im19-link cookie (Secure, HttpOnly, SameSite=Lax,
   Path=/, no Domain) and starts Authorization Code + PKCE at the configured
   MCP issuer using a dedicated confidential browser client.
3. /oauth/browser/callback requires the browser cookie and single-use state.
   The server exchanges the code over a pinned HTTPS token endpoint. Bounded
   introspection must prove the expected issuer/resource/scope/expiry and the
   exact original MCP subject AND dedicated browser client ID. Identity
   mismatch stops the flow before any Google consent URL is created. The
   browser access token is neither stored nor returned.
4. Only after browser identity is proven does IM19 create Google's PKCE state,
   bound to the same browser cookie, MCP subject and selected service. Google
   redirects to /oauth/google/callback. A missing or different cookie, wrong
   purpose, expired/replayed state or insufficient scopes is rejected.
5. The callback stages encrypted Google credentials and shows a one-time code
   valid for five minutes. It does not activate the connection. The originating
   MCP subject explicitly submits that code to confirm_service_authorization.
6. Pending-code consumption, same-account-only credential activation and the
   success audit commit in one database transaction. Transaction failure leaves
   the valid code retriable. A concurrent first link cannot rebind an account.
7. get_connection_status reports stored credential status. A supported provider
   read is required to verify live Google connectivity.

Configure the dedicated browser client and exact redirects using DEPLOYMENT.md.
This flow trusts resource-bound access-token introspection as the identity
proof; it does not consume or claim to validate OIDC ID tokens. The configured
issuer must provide authoritative stable user sub values for these grants.
Complete live acceptance with that issuer before production deployment.

No browser/code flow replaces a human's review of requested scopes. Never
share tickets or confirmation codes. Complete one flow per browser at a time;
starting another replaces the cookie and safely invalidates the earlier flow.
Every flow/state expires after ten minutes, and browser cookies are deleted on
the final successful Google callback. Pending confirmation lasts five minutes.

YouTube defaults to youtube.readonly; analytics is separate. Drive defaults
to drive.metadata.readonly; reading text additionally requires drive.readonly.
Review Google's restricted-scope verification requirements before public use.
Each service has its own encrypted credential. Production credentials must
carry browser proof; legacy credentials require reauthorization.

Direct Google consent with MCP-code confirmation remains available only for
explicit controlled staging/local development. It cannot be used in the
production gateway. Revocation/disconnect remains a trusted-backend operation;
no provider write or account revocation tool is exposed to the model.

All upstream OAuth responses are bounded; automatic token-exchange retries
are avoided. Refresh uses shared DB leases and CAS updates. REFRESH_IN_PROGRESS
is retriable later, never permission to start competing refresh attempts.
Run im19-purge-expired on a monitored backend schedule. Keep callback queries,
provider tokens and PKCE verifiers out of logs and follow KEY_ROTATION.md.

Reference: https://developers.google.com/identity/protocols/oauth2/web-server
