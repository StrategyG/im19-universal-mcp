# IM19 Universal MCP — GitHub Codespaces + Codex

This workflow launches the real IM19 MCP as a **local stdio subprocess**.
It does NOT configure Google OAuth, open a public HTTP endpoint, or grant an
AI access to GitHub automatically.

## 1. Create your Codespace

Open https://github.com/StrategyG/im19-universal-mcp and choose
**Code > Codespaces > Create codespace** on branch feat/codespaces-codex-runtime
(until this pull request is merged, then use main).

The new .devcontainer/devcontainer.json selects Python 3.12 and Node.js 22,
prevents automatic forwarding of the IM19 gateway's port, and runs the
bootstrap script when the container is created.

For an existing Codespace, switch to the feature branch and run
**Codespaces: Rebuild Container** using the VS Code command palette.
If you already have Python 3.11 or newer, you can bootstrap manually:

    bash scripts/codespaces/bootstrap.sh

This creates .venv, installs the package, validates imports, and launches a
real stdio subprocess smoke test. It does NOT request any provider secrets.

## 2. Check the running MCP

    .venv/bin/python scripts/codespaces/probe.py
    .venv/bin/python -m pytest -q tests/test_mcp.py tests/test_codespaces_runtime.py

Expected markers are IM19_CODESPACES_MCP_PASS, transport=stdio,
drive_status=AUTH_REQUIRED and google_live_verified=false.

AUTH_REQUIRED is the CORRECT response until real OAuth authentication is
completed. Successful CI is not proof that any user's Codespace is online.

## 3. Install / authenticate Codex CLI (only if you choose to)

If the Codex CLI is absent:

    npm install -g @openai/codex@latest

Authenticate interactively with a device code in your browser:

    codex login --device-auth
    codex login status

Keep device codes, OAuth credentials, API keys and Codex auth cache secret;
never paste them into chat, a repository or logs.

Register the local MCP to your own Codex settings:

    bash scripts/codespaces/register_codex.sh
    codex mcp list
    codex

Inside the Codex terminal UI, enter /mcp to inspect available servers.

The registration command does not override an existing server named im19.
Check it first with codex mcp get im19. If necessary remove it explicitly
using codex mcp remove im19, then re-run the registration script.

The launcher is safe-by-default: it runs using env -i to avoid inheriting
OpenAI, GitHub, Google or IM19 secrets from Codespaces. It does not send
private credentials to any AI model or store a real OAuth grant.

## 4. Troubleshooting

- ModuleNotFoundError: im19_mcp: rerun bootstrap, use .venv/bin/python rather
  than system python; verify the source directory exists under src/im19_mcp.
- codex: command not found: rebuild the dev container (Node 22), then
  explicitly install Codex CLI using the command above.
- MCP listed but no GitHub repository: the IM19 connector does not implement
  GitHub repository discovery. Open the repository folder in your VS Code
  Codespace; use Codex there for local source code access.
- AUTH_REQUIRED: no valid MCP principal or Google provider authorization;
  this is not an import error.
- Codespace slept or stopped: this is an on-demand dev environment; the stdio
  MCP server exists only while its client is running.

## 5. Remote HTTPS / ChatGPT is a separate project phase

Do not expose the IM19 gateway to the public internet until you provision
stable HTTPS ingress, a real MCP OAuth issuer + introspection, PostgreSQL
migrations through 0005_distributed_quotas, encryption key custody,
Google OAuth consent/callback, trusted WAF, and end-to-end user binding.

A Codespaces forwarded port is NOT a production identity provider.
Do not mark a forwarded IM19 port as Public to bypass OAuth security.
For full prerequisites see DEPLOYMENT.md and CHATGPT_INTEGRATION.md.

## CI evidence limitations

GitHub Actions tests MCP stdio subprocess, Python module import,
the Codespaces devcontainer configuration, and bash syntax.
These tests do NOT execute the actual Codespaces image build or interactive
Codex login. Real Google/ChatGPT connectivity is still unverified.

IM19 currently exposes safe read-only Google Drive and YouTube methods,
not automatic publishing, trading, file deletion or any Google write tool.
