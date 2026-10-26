"""Redacted local/remote startup checks; not a provider connectivity test.

The command never prints configuration values and never contacts a provider.
Database checks are opt-in because they can open a network connection.
"""
from __future__ import annotations

import argparse
import json
import sys
from importlib.util import find_spec
from typing import Any

from cryptography.fernet import Fernet

from im19_mcp.database import database_engine

from im19_mcp.config import Settings
from im19_mcp.readiness import database_ready


def diagnose(*, mode: str, check_database: bool = False) -> dict[str, Any]:
    """Return boolean checks only; never include secrets or exception strings."""
    if mode not in {"local", "remote"}:
        raise ValueError("mode_must_be_local_or_remote")
    checks: dict[str, bool] = {
        "python_supported": sys.version_info >= (3, 11),
        "package_importable": find_spec("im19_mcp") is not None,
    }
    config: Settings | None = None
    try:
        config = Settings.from_env()
        if mode == "remote":
            config.require_remote()
        if config.vault_key:
            Fernet(config.vault_key.encode("ascii"))
        checks["configuration_valid"] = True
    except (ValueError, UnicodeError, TypeError):
        checks["configuration_valid"] = False
    if check_database:
        checks["database_schema_ready"] = False
        if config is not None and checks["configuration_valid"]:
            try:
                engine = database_engine(config.database_url)
                try:
                    checks["database_schema_ready"] = bool(database_ready(engine))
                finally:
                    engine.dispose()
            except Exception:
                # Fail closed; exception strings often contain DB URLs or secrets.
                checks["database_schema_ready"] = False
    return {
        "mode": mode,
        "checks": checks,
        "passed": all(checks.values()),
        "database_checked": check_database,
        "live_provider_verified": False,
        "chatgpt_connected_verified": False,
        "production_acceptance": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Redacted IM19 MCP configuration preflight")
    parser.add_argument("--mode", choices=("local", "remote"), default="local")
    parser.add_argument("--check-database", action="store_true",
                        help="Actively test DB connectivity and Alembic schema revision")
    args = parser.parse_args(argv)
    report = diagnose(mode=args.mode, check_database=args.check_database)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

