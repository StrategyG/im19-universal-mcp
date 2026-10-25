#!/usr/bin/env bash
# Pure stdio transport: stdout is RESERVED for MCP JSON-RPC.
# Use bash <path> instead of executing directly; GitHub Contents
# API stores non-executable mode by default.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
    printf 'IM19 Python venv missing; run bash scripts/codespaces/bootstrap.sh\n' >&2
    exit 1
fi

# No GitHub/Codex API keys, Google credentials or shell-exported IM19_*
# settings are inherited. Only metadata tools work without valid OAuth.
exec env -i \
    "PATH=$PATH" \
    "HOME=$HOME" \
    "LANG=C.UTF-8" \
    "PYTHONPATH=$ROOT/src" \
    "IM19_DEPLOYMENT_ENV=development" \
    "$PY" -m im19_mcp.server
