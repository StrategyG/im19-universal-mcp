"""IM19 bounded autonomous workflow controller: deterministic planner + durable DAG.

Only an explicit, allowlisted ARUSIA metadata template is supported. This is
NOT an LLM runtime or media worker, and does not accept arbitrary code, URLs,
providers or shell commands from an AI plan.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint, func, select, update
from sqlalchemy.orm import Mapped, mapped_column

from im19_mcp.arusia.workflow import ArusiaWorkflow, WorkflowError, parse_manifest, preview
from im19_mcp.storage import Base, Vault

PLAN_VERSION = "im19.workflow.v1"
GOAL = "prepare_arusia_episode"
ALIASES = frozenset((GOAL, "siapkan episode arusia", "prepare arusia episode"))
NAME = re.compile(r"[a-z][a-z0-9_]{1,39}")
ID = re.compile(r"[0-9a-f]{32}")
MAX_PLAN_BYTES = 170_000
MAX_RESULT_BYTES = 32_000
LEASE_SECONDS = 90
MAX_ATTEMPTS = 3


class AutonomousError(ValueError):
    """Expected denial reason, without exposing database or worker credentials."""


class WorkflowRun(Base):
    __tablename__ = "im19_autonomous_runs"
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (UniqueConstraint("subject", "plan_sha256",
                                       name="uq_im19_owner_compiled_plan"),)


class WorkflowStep(Base):
    __tablename__ = "im19_autonomous_steps"
    step_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("im19_autonomous_runs.run_id"),
                                         nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    depends_json: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    retry_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    __table_args__ = (UniqueConstraint("run_id", "key", name="uq_im19_autonomous_step_key"),)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _valid_subject(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        raise AutonomousError("verified_subject_required")
    return value


def _run_id(value: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise AutonomousError("run_id_invalid")
    return value


def prepare_plan(goal: str, manifest_json: str) -> dict:
    """Rule-based plan synthesis. LLM-proposed plans must pass same compiler."""
    if not isinstance(goal, str) or goal.strip().lower() not in ALIASES:
        raise AutonomousError("unsupported_goal_no_planner_template")
    manifest, checksum = parse_manifest(manifest_json)
    manifest_text = _canonical(manifest)
    plan = {
        "schema_version": PLAN_VERSION,
        "goal": GOAL,
        "nodes": [
            {"id": "inspect_manifest", "operation": "arusia.manifest_preview",
             "depends_on": [], "arguments": {"manifest_json": manifest_text}},
            {"id": "register_episode", "operation": "arusia.episode_register",
             "depends_on": ["inspect_manifest"], "arguments": {"manifest_json": manifest_text}},
        ],
    }
    return {
        "status": "PLAN_VALID", "plan_json": _canonical(plan),
        "plan_sha256": hashlib.sha256(_canonical(plan).encode("utf-8")).hexdigest(),
        "manifest_sha256": checksum, "steps": 2, "external_side_effects": False,
        "media_generated": False, "planner": "allowlisted_deterministic_template",
        "needs_explicit_start": True,
    }


def compile_plan(plan_json: str) -> tuple[dict, str]:
    """Fail-closed compiler: reject modified steps, dependencies or arguments."""
    if not isinstance(plan_json, str) or len(plan_json.encode("utf-8")) > MAX_PLAN_BYTES:
        raise AutonomousError("plan_size_invalid")
    try:
        plan = json.loads(plan_json)
    except (ValueError, TypeError, RecursionError):
        raise AutonomousError("plan_json_invalid") from None
    if not isinstance(plan, dict) or set(plan) != {"schema_version", "goal", "nodes"}:
        raise AutonomousError("plan_schema_invalid")
    if plan.get("schema_version") != PLAN_VERSION or plan.get("goal") != GOAL:
        raise AutonomousError("plan_schema_invalid")
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 2:
        raise AutonomousError("plan_nodes_invalid")
    if any(not isinstance(node, dict) or set(node) != {
        "id", "operation", "depends_on", "arguments",
    } for node in nodes):
        raise AutonomousError("plan_node_schema_invalid")
    try:
        source = nodes[0]["arguments"]["manifest_json"]
        expected = json.loads(prepare_plan(GOAL, source)["plan_json"])
    except (KeyError, TypeError, ValueError, WorkflowError, AutonomousError):
        raise AutonomousError("plan_manifest_invalid") from None
    if plan != expected:
        raise AutonomousError("plan_not_allowlisted")
    checksum = hashlib.sha256(_canonical(plan).encode("utf-8")).hexdigest()
    return expected, checksum


@dataclass(frozen=True)
class AutonomousEngine:
    vault: Vault

    def create(self, subject: str, plan_json: str) -> dict:
        _valid_subject(subject)
        plan, checksum = compile_plan(plan_json)
        with self.vault.sessions.begin() as session:
            existing = session.execute(select(WorkflowRun).where(
                WorkflowRun.subject == subject, WorkflowRun.plan_sha256 == checksum,
            )).scalar_one_or_none()
            if existing is not None:
                return {"status": "EXISTING", "run_id": existing.run_id,
                        "workflow_status": existing.status, "plan_sha256": checksum}
            run_id = secrets.token_hex(16)
            now = time.time()
            session.add(WorkflowRun(run_id=run_id, subject=subject, plan_sha256=checksum,
                                    plan_json=_canonical(plan), status="DRAFT",
                                    created_at=now, updated_at=now))
            for node in plan["nodes"]:
                session.add(WorkflowStep(
                    step_id=secrets.token_hex(16), run_id=run_id,
                    key=node["id"], operation=node["operation"],
                    depends_json=_canonical(node["depends_on"]),
                    arguments_json=_canonical(node["arguments"]),
                    status="BLOCKED",
                ))
        return {"status": "CREATED", "run_id": run_id, "plan_sha256": checksum,
                "workflow_status": "DRAFT", "automatic_execution_started": False}

    def action(self, subject: str, run_id: str, action: str) -> dict:
        """Explicit owner-triggered lifecycle transition; never provider writes."""
        _valid_subject(subject)
        _run_id(run_id)
        if action not in {"start", "pause", "resume", "cancel"}:
            raise AutonomousError("workflow_action_not_supported")
        allowed = {
            "start": ("DRAFT", "RUNNING"),
            "pause": ("RUNNING", "PAUSED"),
            "resume": ("PAUSED", "RUNNING"),
            "cancel": (("DRAFT", "RUNNING", "PAUSED", "FAILED"), "CANCELLED"),
        }
        before, after = allowed[action]
        before_states = (before,) if isinstance(before, str) else before
        now = time.time()
        with self.vault.sessions.begin() as session:
            row = session.execute(select(WorkflowRun).where(
                WorkflowRun.run_id == run_id, WorkflowRun.subject == subject,
            ).with_for_update()).scalar_one_or_none()
            if row is None:
                raise AutonomousError("workflow_not_found")
            if row.status not in before_states:
                raise AutonomousError("workflow_transition_denied")
            row.status = after
            row.updated_at = now
            if action == "start":
                # Compiler guarantees only one root, and no human-protected steps.
                session.execute(update(WorkflowStep).where(
                    WorkflowStep.run_id == run_id, WorkflowStep.key == "inspect_manifest",
                    WorkflowStep.status == "BLOCKED",
                ).values(status="READY"))
            elif action == "cancel":
                session.execute(update(WorkflowStep).where(
                    WorkflowStep.run_id == run_id,
                    WorkflowStep.status.in_(("READY", "BLOCKED", "LEASED")),
                ).values(status="CANCELLED", lease_hash="", lease_until=0.0))
        return {"status": after, "run_id": run_id, "external_side_effects": False}

    def status(self, subject: str, run_id: str) -> dict:
        """Tenant-scoped, bounded state summary. Never expose worker lease tokens."""
        _valid_subject(subject)
        _run_id(run_id)
        with self.vault.sessions() as session:
            row = session.execute(select(WorkflowRun).where(
                WorkflowRun.run_id == run_id, WorkflowRun.subject == subject,
            )).scalar_one_or_none()
            if row is None:
                raise AutonomousError("workflow_not_found")
            steps = session.execute(select(WorkflowStep).where(
                WorkflowStep.run_id == run_id,
            ).order_by(WorkflowStep.key)).scalars().all()
            return {
                "status": row.status, "run_id": run_id, "plan_sha256": row.plan_sha256,
                "steps": [{
                    "id": s.key, "operation": s.operation, "status": s.status,
                    "attempts": s.attempts,
                    "result": json.loads(s.result_json) if s.status == "DONE" else None,
                } for s in steps],
                "media_generated": False,
                "episode_render_verified": False,
                "executor": "trusted_local_allowlisted_worker_only",
            }

    def _claim(self, subject: str, run_id: str) -> dict | None:
        """Atomic CAS lease; failed or expired tasks do not require session state."""
        now = time.time()
        with self.vault.sessions.begin() as session:
            row = session.execute(select(WorkflowRun).where(
                WorkflowRun.run_id == run_id, WorkflowRun.subject == subject,
                WorkflowRun.status == "RUNNING",
            )).scalar_one_or_none()
            if row is None:
                return None
            candidates = session.execute(select(WorkflowStep).where(
                WorkflowStep.run_id == run_id,
                WorkflowStep.attempts < MAX_ATTEMPTS,
                ((WorkflowStep.status == "READY") |
                 ((WorkflowStep.status == "LEASED") & (WorkflowStep.lease_until <= now))),
                WorkflowStep.retry_at <= now,
            ).order_by(WorkflowStep.key).limit(5)).scalars().all()
            for step in candidates:
                token = secrets.token_urlsafe(32)
                query = update(WorkflowStep).where(
                    WorkflowStep.step_id == step.step_id,
                    WorkflowStep.attempts < MAX_ATTEMPTS,
                    ((WorkflowStep.status == "READY") |
                     ((WorkflowStep.status == "LEASED") & (WorkflowStep.lease_until <= now))),
                    WorkflowStep.retry_at <= now,
                ).values(status="LEASED", attempts=WorkflowStep.attempts + 1,
                         lease_hash=hashlib.sha256(token.encode()).hexdigest(),
                         lease_until=now + LEASE_SECONDS)
                if session.execute(query).rowcount == 1:
                    return {"run_id": run_id, "step_id": step.step_id, "key": step.key,
                            "operation": step.operation, "arguments": json.loads(step.arguments_json),
                            "lease_token": token}
        return None

    def _finish(self, subject: str, task: dict, result: dict | None,
                *, failure: bool = False) -> dict:
        # Do not permit model-provided result fields or forged worker receipts.
        if result is not None:
            encoded = _canonical(result)
            if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
                raise AutonomousError("worker_result_too_large")
        now = time.time()
        with self.vault.sessions.begin() as session:
            run = session.execute(select(WorkflowRun).where(
                WorkflowRun.run_id == task["run_id"], WorkflowRun.subject == subject,
            ).with_for_update()).scalar_one_or_none()
            if run is None or run.status != "RUNNING":
                return {"status": "WORKFLOW_NOT_RUNNING"}
            step = session.execute(select(WorkflowStep).where(
                WorkflowStep.step_id == task["step_id"], WorkflowStep.run_id == run.run_id,
            )).scalar_one_or_none()
            if (step is None or step.status != "LEASED" or
                    step.lease_until <= now or
                    step.lease_hash != hashlib.sha256(task["lease_token"].encode()).hexdigest()):
                return {"status": "LEASE_EXPIRED_OR_INVALID"}
            step.lease_hash = ""
            step.lease_until = 0.0
            run.updated_at = now
            if failure:
                # No blind replays of provider mutation: handlers are local
                # deterministic metadata operations, and episode registration
                # is idempotent by owner and canonical manifest.
                if step.attempts >= MAX_ATTEMPTS:
                    step.status = "FAILED"
                    run.status = "FAILED"
                else:
                    step.status = "READY"
                    step.retry_at = now + min(60, 5 * 2 ** (step.attempts - 1))
                return {"status": step.status, "attempts": step.attempts}
            step.status = "DONE"
            step.result_json = encoded
            # Unlock only nodes with all declared predecessors marked DONE.
            all_steps = session.execute(select(WorkflowStep).where(
                WorkflowStep.run_id == run.run_id,
            )).scalars().all()
            states = {item.key: item.status for item in all_steps}
            for successor in all_steps:
                if successor.status != "BLOCKED":
                    continue
                deps = json.loads(successor.depends_json)
                if deps and all(states.get(key) == "DONE" for key in deps):
                    successor.status = "READY"
            if all(item.status == "DONE" for item in all_steps):
                run.status = "COMPLETE"
            return {"status": "DONE", "step": step.key,
                    "workflow_status": run.status}

    def run_once(self, subject: str, run_id: str) -> dict:
        """Trusted LOCAL service loop; never registered as an MCP tool."""
        _valid_subject(subject)
        _run_id(run_id)
        task = self._claim(subject, run_id)
        if task is None:
            with self.vault.sessions.begin() as session:
                run = session.execute(select(WorkflowRun).where(
                    WorkflowRun.run_id == run_id, WorkflowRun.subject == subject,
                ).with_for_update()).scalar_one_or_none()
                if run is None:
                    raise AutonomousError("workflow_not_found")
                if run.status != "RUNNING":
                    return {"status": run.status}
                exhausted = session.execute(select(WorkflowStep).where(
                    WorkflowStep.run_id == run_id, WorkflowStep.attempts >= MAX_ATTEMPTS,
                    WorkflowStep.status == "LEASED", WorkflowStep.lease_until <= time.time(),
                )).scalars().all()
                if exhausted:
                    for step in exhausted:
                        step.status = "FAILED"
                    run.status = "FAILED"
                    return {"status": "FAILED", "reason": "retry_budget_exhausted"}
            return {"status": "NO_READY_WORK", "reason": "blocked_or_backoff"}
        try:
            if task["operation"] == "arusia.manifest_preview":
                output = preview(task["arguments"]["manifest_json"])
            elif task["operation"] == "arusia.episode_register":
                output = ArusiaWorkflow(self.vault).create(
                    subject, task["arguments"]["manifest_json"],
                )
            else:
                raise AutonomousError("executor_operation_not_allowlisted")
            return self._finish(subject, task, output)
        except (WorkflowError, AutonomousError):
            # Invalid user plans and idempotency conflicts are terminal.
            result = self._finish(subject, task, None, failure=True)
            return {"status": result["status"], "reason": "task_execution_failed"}
        except (OSError, TimeoutError):
            result = self._finish(subject, task, None, failure=True)
            return {"status": result["status"], "reason": "temporary_worker_failure"}
