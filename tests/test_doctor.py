"""Checks fail-closed and redaction behavior of the diagnostic CLI."""
from __future__ import annotations

import json

from im19_mcp.doctor import diagnose, main


def test_local_doctor_without_credentials(monkeypatch):
    monkeypatch.setenv("IM19_DATABASE_URL", "sqlite:///./doctor-local.sqlite3")
    monkeypatch.delenv("IM19_FERNET_KEY", raising=False)
    report = diagnose(mode="local")
    assert report["passed"] is True
    assert report["database_checked"] is False
    assert report["live_provider_verified"] is False
    assert report["production_acceptance"] is False


def test_remote_fails_without_credentials_and_does_not_probe_db(monkeypatch, capsys):
    monkeypatch.setenv("IM19_DATABASE_URL", "postgresql+psycopg://user:TOP_SECRET@localhost:1/db")
    monkeypatch.delenv("IM19_MCP_ISSUER", raising=False)
    monkeypatch.delenv("IM19_FERNET_KEY", raising=False)
    assert main(["--mode", "remote", "--check-database"]) == 1
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["checks"]["configuration_valid"] is False
    assert result["checks"]["database_schema_ready"] is False
    assert "TOP_SECRET" not in output
    assert "localhost" not in output


def test_invalid_local_fernet_key_fails_redacted(monkeypatch, capsys):
    monkeypatch.setenv("IM19_FERNET_KEY", "TOP_SECRET_INVALID_KEY")
    assert main(["--mode", "local"]) == 1
    output = capsys.readouterr().out
    assert "TOP_SECRET_INVALID_KEY" not in output
    assert json.loads(output)["checks"]["configuration_valid"] is False


def test_mode_validation_is_explicit():
    import pytest
    with pytest.raises(ValueError, match="mode_must_be_local_or_remote"):
        diagnose(mode="other")
