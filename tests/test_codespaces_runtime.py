"""Regression tests for the Codespaces/Codex local stdio MCP workflow.

The subprocess test uses no live Google/ChatGPT account and does not
start a network listener. It does not test a real Codespaces image build.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODESPACES = ROOT / "scripts" / "codespaces"


def test_codespaces_devcontainer_is_cpu_safe_and_reproducible():
    path = ROOT / ".devcontainer" / "devcontainer.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert "python:1-3.12" in cfg["image"]
    assert cfg["features"]["ghcr.io/devcontainers/features/node:1"]["version"] == "22"
    assert cfg["forwardPorts"] == []
    assert cfg["portsAttributes"]["8000"]["onAutoForward"] == "ignore"
    assert cfg["postCreateCommand"] == "bash scripts/codespaces/bootstrap.sh"
    assert cfg["customizations"]["vscode"]["settings"]["python.defaultInterpreterPath"].endswith(
        "/.venv/bin/python"
    )


def test_codespaces_scripts_parse_and_do_not_enable_public_gateway():
    for name in ("bootstrap.sh", "launch_stdio.sh", "register_codex.sh"):
        path = CODESPACES / name
        completed = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True,
            timeout=10, check=False,
        )
        assert completed.returncode == 0, completed.stderr
        body = path.read_text(encoding="utf-8")
        assert "ngrok" not in body.lower()
        assert "cloudflared" not in body.lower()
        assert "im19-gateway" not in body
    launcher = (CODESPACES / "launch_stdio.sh").read_text(encoding="utf-8")
    assert "exec env -i" in launcher
    assert "IM19_DEPLOYMENT_ENV=development" in launcher
    assert "-m im19_mcp.server" in launcher
    registration = (CODESPACES / "register_codex.sh").read_text(encoding="utf-8")
    assert "codex mcp add im19 -- bash" in registration
    assert "codex mcp get im19" in registration
    assert "\ncodex mcp remove im19" not in registration  # guidance is okay; no auto-remove


def test_real_mcp_stdio_probe_with_no_oauth_secrets():
    # The script itself whitelists child process variables. Deliberately
    # present fake inherited sensitive variables so the probe proves that
    # it does NOT forward an ambient OAuth principal to the MCP subprocess.
    env = os.environ.copy()
    env.update(
        {
            "IM19_DEV_SUBJECT": "untrusted-ambient-test-subject",
            "IM19_GOOGLE_CLIENT_SECRET": "synthetic-secret-never-real",
            "OPENAI_API_KEY": "synthetic-openai-key-never-real",
            "GITHUB_TOKEN": "synthetic-github-key-never-real",
        }
    )
    completed = subprocess.run(
        [sys.executable, str(CODESPACES / "probe.py")],
        cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=45, check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    output = json.loads(completed.stdout.strip())
    assert output["result"] == "IM19_CODESPACES_MCP_PASS"
    assert output["transport"] == "stdio"
    assert output["tool_count"] >= 8
    assert output["drive_status"] == "AUTH_REQUIRED"
    assert output["google_live_verified"] is False
    assert output["public_http_endpoint"] is False
    for secret in ("synthetic-secret-never-real", "synthetic-openai-key-never-real",
                   "synthetic-github-key-never-real"):
        assert secret not in completed.stdout + completed.stderr
