"""Privileged local IM19 scheduler worker.

Runs only compiled, allowlisted metadata steps. Does not run shell commands,
ComfyUI, Blender, remote scripts, arbitrary URLs or provider writes.
Host OS/service supervision is necessary for 24/7 execution.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time

from sqlalchemy.exc import SQLAlchemyError

from im19_mcp.autonomy.engine import AutonomousEngine, AutonomousError
from im19_mcp.config import Settings
from im19_mcp.storage import Vault


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IM19 allowlisted autonomous metadata worker")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--loop", action="store_true",
                        help="Use with a trusted OS service manager for persistent polling")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args(argv)
    if (not 0.5 <= args.interval <= 60 or
            not re.fullmatch(r"[0-9a-f]{32}", args.run_id)):
        parser.error("worker_arguments_invalid")
    # This is a defensive local host gate, not an external IAM identity proof.
    if os.environ.get("IM19_AUTONOMY_OPERATOR_ACK") != "YES":
        parser.error("local_worker_operator_ack_required")
    if not os.environ.get("IM19_AUTONOMY_SCOPED_SUBJECT") or (
        args.subject != os.environ["IM19_AUTONOMY_SCOPED_SUBJECT"]
    ):
        parser.error("trusted_worker_subject_scope_mismatch")
    cfg = Settings.from_env()
    if not (cfg.vault_key and cfg.enable_arusia_workflows and cfg.enable_autonomous_workflows):
        parser.error("workflow_or_vault_not_configured")
    store = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt)
    engine = AutonomousEngine(store)
    try:
        while True:
            try:
                result = engine.run_once(args.subject, args.run_id)
            except (AutonomousError, SQLAlchemyError, OSError):
                # No sensitive error details or plan contents in logs.
                print(json.dumps({"status": "SERVICE_UNAVAILABLE"}), flush=True)
                return 1
            safe = {"status": result.get("status"),
                    "step": result.get("step"),
                    "workflow_status": result.get("workflow_status")}
            print(json.dumps(safe), flush=True)
            if not args.loop or result["status"] in {
                "COMPLETE", "CANCELLED", "PAUSED", "FAILED",
            } or result.get("workflow_status") == "COMPLETE":
                return 0 if result["status"] in {"DONE", "COMPLETE", "NO_READY_WORK",
                                                 "PAUSED", "CANCELLED"} else 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
