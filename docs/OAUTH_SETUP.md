# Google OAuth Setup — development / controlled staging

1. Configure OAuth consent screen in Google Cloud. Enable YouTube Data API v3
   and YouTube Analytics API for your project, according to Google project rules.
2. Create a **Web application** OAuth client and register the exact callback URL:
   https://YOUR_HOST/oauth/google/callback (localhost HTTP allowed only in dev).
3. Configure IM19_GOOGLE_CLIENT_ID, IM19_GOOGLE_CLIENT_SECRET,
   IM19_GOOGLE_REDIRECT_URI and encryption key in the deployment secret manager.
4. Configure IM19_DATABASE_URL and apply Alembic migrations.
5. A verified MCP user invokes request_service_authorization("youtube").
   A returned authorization_url is a Google consent URL, not a completed login.
6. The browser completes Google consent. Callback validates state (single use),
   exchanges code using PKCE and client credentials, and fetches the Google
   OpenID sub from Google's userinfo endpoint.
7. Credentials are encrypted and mapped to the verified IM19 subject and
   discovered Google account ID. Account swaps require disconnect first.
8. Scope is incremental: include_analytics=true adds Analytics only when requested.

Google is NOT the authorization server for the MCP resource itself.
The MCP resource server uses separate OAuth issuer, audience and token
introspection configuration.

Outstanding for production: Google OAuth app verification if required, managed
key rotation, stronger login-session binding, concurrent refresh locking,
background expiry cleanup, and real connection tests with a test account.

Reference: https://developers.google.com/identity/protocols/oauth2/web-server
