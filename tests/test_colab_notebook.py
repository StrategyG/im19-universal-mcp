"""Static acceptance checks for distributable Colab runtime notebook.

This does not execute pip installation or live MCP/Google integrations.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "colab" / "IM19_Universal_MCP_Colab.ipynb"


def test_notebook_metadata_and_valid_python_source():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    assert notebook["metadata"]["kernelspec"]["name"] == "python3"
    assert len(notebook["cells"]) >= 15
    code_cells = 0
    for index, cell in enumerate(notebook["cells"]):
        assert isinstance(cell["source"], list)
        assert all(isinstance(chunk, str) for chunk in cell["source"])
        if cell["cell_type"] != "code":
            continue
        code_cells += 1
        source = "".join(cell["source"])
        assert cell["execution_count"] is None
        assert cell["outputs"] == []
        ast.parse(source, filename=f"colab-cell-{index}")
    assert code_cells >= 9


def test_notebook_fails_closed_by_default_and_pins_reviewed_commit():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
    assert 'REPO_BRANCH = "main"' in source
    assert 'PINNED_SHA = "514b0ffdb3cc6c5c4e791547402754786419b6ee"' in source
    assert "sys.path.insert(0, src_dir)" in source
    assert 'import im19_mcp' in source
    assert "git" in source and "checkout" in source
    assert "IM19_DEPLOYMENT_ENV" in source
    assert '"development"' in source
    assert "IM19_DEV_SUBJECT" in source
    assert '"IM19_GOOGLE_CLIENT_ID": ""' in source
    assert "ENABLE_PUBLIC_GATEWAY = False" in source
    assert "StdioServerParameters" in source
    assert "from mcp import Client" in source
    assert "alembic" in source and "upgrade" in source
    assert "get_connection_status" in source
    assert "AUTH_REQUIRED" in source
    # No guessed live provider connection or public tunneling tool.
    assert "ngrok" not in source.lower()
    assert "cloudflared" not in source.lower()
    assert "getpass(" not in source


def test_notebook_and_colab_readme_share_pinned_commit():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    sources = "\\n".join("".join(cell["source"]) for cell in notebook["cells"])
    match = re.search(r'PINNED_SHA = "([0-9a-f]{40})"', sources)
    assert match is not None
    readme = (NOTEBOOK.parent / "README.md").read_text(encoding="utf-8")
    assert match.group(1) in readme
    assert "/blob/main/colab/" in readme
    assert "feat/distributed-quotas-v1" not in readme
