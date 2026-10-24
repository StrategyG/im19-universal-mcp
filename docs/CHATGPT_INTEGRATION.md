# ChatGPT custom MCP connection guide (verify current product eligibility)

1. Host IM19 via HTTPS reverse proxy with canonical URL https://YOUR_HOST/mcp.
2. Configure **independent MCP OAuth authorization server**, supporting issuer
   discovery and authorization code + PKCE for the ChatGPT client, and issuing
   access tokens with an audience bound to that exact /mcp resource.
3. Supply IM19_MCP_ISSUER, IM19_MCP_RESOURCE, IM19_MCP_INTROSPECTION,
   IM19_MCP_CLIENT_ID and IM19_MCP_CLIENT_SECRET; configure server-to-server
   token introspection credentials only in a secrets manager.
4. Confirm /.well-known/oauth-protected-resource/mcp advertises the
   expected issuer and scopes, then exercise OAuth login using test credentials.
5. In ChatGPT's supported web workspace/plugin creation interface, add the
   public HTTPS MCP URL, choose OAuth, scan tools, and inspect permissions.
6. Test list_available_services, get_connection_status, and a read-only tool.
   Treat a failed or unsupported login as NOT_CONNECTED, not a successful link.

As documented in October 2026 OpenAI Help, full custom MCP app/write support
is tied to workspace/plan eligibility and web experience. Verify the current
eligibility for your account; do not assume the Android app can create the
connection. A GitHub repository URL is NOT an MCP server URL.

Sources:
- https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt
- https://py.sdk.modelcontextprotocol.io/run/asgi/

Current status: no identity provider, HTTPS deployment or ChatGPT connection
has been configured or live-tested in this repository.
