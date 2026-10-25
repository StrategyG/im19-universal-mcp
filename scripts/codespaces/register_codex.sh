#!/usr/bin/env bash
# Opt-in registration of the local, read-only IM19 development MCP to Codex.
# Does not install Codex, authenticate anyone, or store provider secrets.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo "Install IM19 first: bash scripts/codespaces/bootstrap.sh" >&2
    exit 1
fi
if ! command -v codex >/dev/null 2>&1; then
    echo "Codex CLI not found." >&2
    echo "Install it explicitly with: npm install -g @openai/codex@latest" >&2
    echo "Then authenticate interactively: codex login" >&2
    echo "Then rerun: bash scripts/codespaces/register_codex.sh" >&2
    exit 2
fi

# First verify the actual MCP subprocess without user credentials.
"$ROOT/.venv/bin/python" "$ROOT/scripts/codespaces/probe.py"

if codex mcp get im19 >/dev/null 2>&1; then
    echo "Codex already contains MCP server 'im19'; not overwriting it."
    echo "Inspect with: codex mcp get im19"
    echo "To replace it yourself: codex mcp remove im19"
    exit 0
fi

# Bash launcher clears inherited OPENAI_*, GITHUB_* and IM19_* secrets.
codex mcp add im19 -- bash "$ROOT/scripts/codespaces/launch_stdio.sh"
echo "IM19 MCP registered to Codex (stdio, development-only)."
codex mcp list
echo "Run 'codex', then /mcp to inspect tools."
