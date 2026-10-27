"""Trusted local ARUSIA task operator. NEVER expose as an MCP mutation tool.

The operator must independently verify evidence and control access to this
executable. Nothing in this CLI launches ComfyUI, Blender, FFmpeg, or a browser.
"""
from __future__ import annotations

import argparse
import json
import os

from im19_mcp.arusia.workflow import ArusiaWorkflow, WorkflowError
from im19_mcp.config import Settings
from im19_mcp.storage import Vault


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IM19 trusted ARUSIA workflow operator")
    parser.add_argument("action", choices=["inspect", "claim", "complete", "approve"])
    parser.add_argument("--subject", required=True)
    parser.add_argument("--task-id")
    parser.add_argument("--episode-id")
    parser.add_argument("--worker-id", default="trusted-local-worker")
    parser.add_argument("--lease-token")
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--provenance-json", default="{}")
    parser.add_argument("--evidence-sha256")
    args = parser.parse_args(argv)
    if os.getenv("IM19_ARUSIA_OPERATOR_ACK") != "YES":
        parser.error("trusted_operator_confirmation_required")
    cfg = Settings.from_env()
    if not cfg.enable_arusia_workflows or not cfg.vault_key:
        parser.error("arusia_workflows_or_vault_not_configured")
    vault = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt)
    engine = ArusiaWorkflow(vault)
    try:
        if args.action == "inspect":
            response = engine.inspect(args.subject, args.episode_id or "")
        elif args.action == "claim":
            response = engine.claim(args.subject, args.task_id or "",
                                    worker_id=args.worker_id)
        elif args.action == "complete":
            try:
                provenance = json.loads(args.provenance_json)
            except (ValueError, TypeError):
                raise WorkflowError("provenance_invalid") from None
            response = engine.complete(args.subject, args.task_id or "",
                                       args.lease_token or "", args.artifact_sha256 or "",
                                       provenance=provenance)
        else:
            # Approval is entirely outside the MCP tool surface. The operator
            # must inspect the independent evidence before running this.
            response = engine.approve(args.subject, args.task_id or "",
                                      evidence_sha256=args.evidence_sha256 or "")
        print(json.dumps(response, sort_keys=True))
        return 0
    except (WorkflowError, ValueError):
        print(json.dumps({"status": "DENIED", "reason": "invalid_or_unauthorized_operator_operation"}))
        return 1
    finally:
        vault.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
