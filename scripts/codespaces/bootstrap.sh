#!/usr/bin/env bash
# Reproducible IM19 development bootstrap for GitHub Codespaces.
# No OAuth secrets, browser login, public port or provider account linking.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$ROOT"

if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.12)"
else
    PYTHON_BIN="$(command -v python3)"
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("IM19 requires Python 3.11 or newer")
print("IM19 Codespaces Python:", sys.version.split()[0])
PY

# Never overwrite a virtual environment. Repeating this script is safe.
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi

PY="$ROOT/.venv/bin/python"
"$PY" -m pip install --disable-pip-version-check -e "$ROOT[dev]"
"$PY" - <<'PY'
from pathlib import Path
import im19_mcp
from im19_mcp.readiness import REQUIRED_SCHEMA_REVISION

print("IM19 package imported:", Path(im19_mcp.__file__).resolve())
print("Required database schema:", REQUIRED_SCHEMA_REVISION)
PY

# Synthetically exercise real MCP stdio discovery without any credentials.
"$PY" "$ROOT/scripts/codespaces/probe.py"

echo "IM19 Codespaces local MCP: READY (stdio, unauthenticated provider access)"
echo "Optional Codex setup: bash scripts/codespaces/register_codex.sh"
