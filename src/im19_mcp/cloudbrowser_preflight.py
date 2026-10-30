"""Cloudbrowser deployment preflight: offline by default, redacted, read-only."""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Callable

from im19_mcp.cloudbrowser import CloudbrowserClient
from im19_mcp.config import Settings


async def assess_cloudbrowser(
    *, mode: str = "remote", live: bool = False,
    settings: Settings | None = None,
    client_factory: Callable = CloudbrowserClient,
) -> dict:
    if mode not in {"local", "remote"}:
        raise ValueError("invalid_mode")
    checks = {
        "configuration_valid": False, "connector_configured": False,
        "agent_card_valid": False, "owner_a2a_authenticated": False,
        "database_ready": False, "worker_ready": False,
        "telegram_ready": False, "operational_ready": False,
    }
    result = {"mode": mode, "live_requested": live, "checks": checks,
              "passed": False, "production_acceptance": False,
              "autonomous_write_enabled": False, "status": "NOT_PROBED"}
    try:
        cfg = settings if settings is not None else Settings.from_env()
        if mode == "remote":
            cfg.require_remote()
        else:
            cfg.validate_cloudbrowser()
        checks["configuration_valid"] = True
        checks["connector_configured"] = cfg.cloudbrowser_enabled()
    except (TypeError, UnicodeError, ValueError):
        result["status"] = "INVALID_CONFIGURATION"
        return result
    if not checks["connector_configured"]:
        result["status"] = "NOT_CONFIGURED"
        return result
    if not live:
        return result
    try:
        client = client_factory(cfg.cloudbrowser_url, cfg.cloudbrowser_api_key)
        await client.agent_card()
        checks["agent_card_valid"] = True
        readiness = (await client.readiness())["data"]
        checks["database_ready"] = readiness["checks"]["database"] is True
        checks["worker_ready"] = readiness["checks"]["worker"] is True
        checks["telegram_ready"] = readiness["checks"]["telegram"] is True
        checks["operational_ready"] = readiness["ready"] is True
        # The one-task read-only query proves authentication; never expose
        # task contents, message text, IDs or artifact locations.
        await client.list_task_summaries(page_size=1)
        checks["owner_a2a_authenticated"] = True
    except Exception:
        # Never emit URL, bearer, upstream bodies or exception messages.
        result["status"] = "LIVE_PROBE_FAILED"
        return result
    result["passed"] = all(checks.values())
    result["status"] = ("LIVE_READONLY_VERIFIED" if result["passed"]
                        else "LIVE_NOT_OPERATIONALLY_READY")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Redacted Cloudbrowser preflight")
    parser.add_argument("--mode", choices=("local", "remote"), default="remote")
    parser.add_argument("--live", action="store_true",
                        help="Explicitly opt into bounded read-only HTTP probes")
    args = parser.parse_args(argv)
    report = asyncio.run(assess_cloudbrowser(mode=args.mode, live=args.live))
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
