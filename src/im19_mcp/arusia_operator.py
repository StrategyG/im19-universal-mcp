"""Privileged host-side ARUSIA operator: NOT an MCP tool or remote API.

Requires an independently secured operator host and artifact root. The local
acknowledgement/environment variables are configuration checks, *not* identity
verification. Production needs worker identity/IAM and a human approval portal.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from im19_mcp.arusia.artifacts import ArtifactRegistry
from im19_mcp.arusia.workflow import ArusiaWorkflow, WorkflowError
from im19_mcp.config import Settings
from im19_mcp.storage import Vault


def _read_lease(path: str | None) -> str:
    if not path:
        raise WorkflowError("lease_file_required")
    p = Path(path)
    if p.is_symlink():
        raise WorkflowError("lease_file_symlink_forbidden")
    info = p.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise WorkflowError("lease_file_permissions_invalid")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise WorkflowError("lease_file_owner_invalid")
    token = p.read_text().strip()
    if not 30 <= len(token) <= 128:
        raise WorkflowError("lease_file_invalid")
    return token


def _write_lease(path: str | None, token: str) -> None:
    if not path:
        raise WorkflowError("lease_file_required")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token)
        handle.flush()
        os.fsync(handle.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IM19 privileged ARUSIA operator (local only)")
    parser.add_argument("action", choices=["inspect", "claim", "complete", "approve",
                                            "ingest", "approve-asset"])
    parser.add_argument("--subject", required=True)
    parser.add_argument("--task-id")
    parser.add_argument("--episode-id")
    parser.add_argument("--worker-id", default="trusted-local-worker")
    parser.add_argument("--lease-token-file")
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--provenance-json", default="{}")
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--asset-id")
    parser.add_argument("--source-name")
    args = parser.parse_args(argv)
    if os.getenv("IM19_ARUSIA_OPERATOR_ACK") != "YES":
        parser.error("privileged_operator_confirmation_required")
    # Prevent accidents across project tenants; this is not an IAM guarantee.
    if not os.getenv("IM19_ARUSIA_SCOPED_SUBJECT") or args.subject != os.getenv(
        "IM19_ARUSIA_SCOPED_SUBJECT"
    ):
        parser.error("operator_subject_scope_mismatch")
    cfg = Settings.from_env()
    root = os.getenv("IM19_ARUSIA_ARTIFACT_ROOT", "")
    if not cfg.enable_arusia_workflows or not cfg.vault_key or not root:
        parser.error("arusia_workflows_vault_or_artifact_root_not_configured")
    vault = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt)
    engine = ArusiaWorkflow(vault, root)
    try:
        registry = ArtifactRegistry(vault, root)
        if args.action == "inspect":
            response = engine.inspect(args.subject, args.episode_id or "")
        elif args.action == "ingest":
            response = registry.ingest(args.subject, args.asset_id or "", args.source_name or "")
        elif args.action == "approve-asset":
            response = registry.approve(args.subject, args.asset_id or "",
                                        args.artifact_sha256 or "",
                                        args.evidence_sha256 or "")
        elif args.action == "claim":
            if not args.lease_token_file or Path(args.lease_token_file).exists():
                raise WorkflowError("unique_lease_token_file_required")
            response = engine.claim(args.subject, args.task_id or "",
                                    worker_id=args.worker_id)
            _write_lease(args.lease_token_file, response.pop("lease_token"))
            response["lease_token_written_to_file"] = True
        elif args.action == "complete":
            try:
                provenance = json.loads(args.provenance_json)
            except (ValueError, TypeError):
                raise WorkflowError("provenance_invalid") from None
            response = engine.complete(args.subject, args.task_id or "",
                                       _read_lease(args.lease_token_file),
                                       args.artifact_sha256 or "", provenance=provenance)
        else:
            # Independent visual evidence must be inspected by a privileged
            # human before running this CLI. Hash verification checks bytes,
            # not the semantic correctness of the creative decision.
            response = engine.approve(args.subject, args.task_id or "",
                                      evidence_sha256=args.evidence_sha256 or "")
        vault.audit(args.subject, "arusia", args.action, response["status"])
        print(json.dumps(response, sort_keys=True))
        return 0
    except (WorkflowError, ValueError, OSError):
        print(json.dumps({"status": "DENIED",
                          "reason": "invalid_or_unauthorized_operator_operation"}))
        return 1
    finally:
        vault.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
