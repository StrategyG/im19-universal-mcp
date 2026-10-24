"""Static acceptance checks for distributable Colab runtime notebook.

This does not execute pip installation or live MCP/Google integrations.
"""
from __future__ import annotations

import ast
import json
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
    assert "e60d8276f6cb0b87b30fb04d0819f788dcad2544" in source
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


def test_colab_bootstrap_resolves_kernel_and_subprocess_module_paths():
    """Regression for ModuleNotFoundError in Colab's migration cell."""
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    code = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
    bootstrap = next(cell for cell in code if 'f"{REPO_DIR}[dev]"' in cell and "IM19 IMPORT" in cell)
    runtime = next(cell for cell in code if "LOCAL_SETTINGS = {" in cell)
    migration = next(cell for cell in code if 'database_ready(db_engine)' in cell)
    assert 'sys.executable, "-m", "pip", "install"' in bootstrap
    assert "sys.path.insert(0, str(REPO_SRC))" in bootstrap
    assert "importlib.invalidate_caches()" in bootstrap
    assert "from im19_mcp.readiness import REQUIRED_SCHEMA_REVISION" in bootstrap
    assert 'LOCAL_ENV["PYTHONPATH"] = str(REPO_SRC)' in runtime
    assert "sys.path.insert(0, str(REPO_SRC))" in migration
    assert 'LOCAL_ENV["PYTHONPATH"] = str(REPO_SRC)' in migration
    assert 'sys.executable, "-m", "alembic", "upgrade", "head"' in migration
    assert "Sel runtime lokal belum dijalankan" in migration
