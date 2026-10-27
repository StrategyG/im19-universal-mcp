"""ARUSIA v9: deterministic episode planning and PostgreSQL-backed task DAG.

This module does NOT generate images, claim asset presence or render MP4. Only
trusted, separately authenticated workers/operators can advance workflow state.
MCP tools create/inspect plans, never approve quality or execute providers.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column

from im19_mcp.storage import Base, Vault

CODE = re.compile(r"S[0-9]{2}E[0-9]{2}")
SHOT = re.compile(r"SH[0-9]{3}")
ASSET = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
DIGEST = re.compile(r"[0-9a-f]{64}")
CHARACTERS = frozenset({"KIO", "LUMI", "ARKA", "MAYOR", "RUX"})
# Workflow stages are execution contracts; no remote API side effects here.
STAGES = (
    ("SCRIPT_BREAKDOWN", "WORKER", "STORY_ENGINE"),
    ("ASSET_LOCK", "HUMAN", "ASSET_REGISTRY"),
    ("STORYBOARD", "WORKER", "STORY_ENGINE"),
    ("CREATIVE_APPROVAL", "HUMAN", "CREATIVE_REVIEW"),
    ("POSE_CAMERA", "WORKER", "SHOT_CONTROLLER"),
    ("IMAGE_GENERATION", "WORKER", "PRECISION_IMAGE_ENGINE"),
    ("FRAME_QC", "HUMAN", "QUALITY_GOVERNOR"),
    ("ANIMATION", "WORKER", "ANIMATION_ENGINE"),
    ("AUDIO", "WORKER", "AUDIO_ENGINE"),
    ("SHOT_QC", "HUMAN", "QUALITY_GOVERNOR"),
)
# A full-episode deliverable cannot start before EVERY shot passes SHOT_QC.
EPISODE_KEY = "EPISODE"
EPISODE_STAGES = (
    ("EPISODE_COMPOSITE", "WORKER", "RENDER_ENGINE"),
    ("EPISODE_FINAL_QC", "HUMAN", "QUALITY_GOVERNOR"),
    ("DELIVERY_PACKAGE", "WORKER", "RENDER_ENGINE"),
)
STAGE_INDEX = {name: index for index, (name, _, _) in enumerate(STAGES)}
MAX_SHOTS = 100
MAX_MANIFEST_BYTES = 160_000


class WorkflowError(ValueError):
    """Safe, stable error reason for the MCP boundary."""


class ArusiaEpisode(Base):
    __tablename__ = "arusia_episodes"
    episode_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (UniqueConstraint("subject", "code", name="uq_arusia_owner_episode_code"),)


class ArusiaTask(Base):
    __tablename__ = "arusia_tasks"
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(36), ForeignKey("arusia_episodes.episode_id"),
                                            nullable=False, index=True)
    shot_id: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    output_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    __table_args__ = (UniqueConstraint("episode_id", "shot_id", "position",
                                       name="uq_arusia_shot_stage"),)


def _string(value: Any, pattern: re.Pattern[str], reason: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise WorkflowError(reason)
    return value


def parse_manifest(payload: str) -> tuple[dict[str, Any], str]:
    """Strict, bounded schema; unknown fields rejected to avoid silent drift."""
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise WorkflowError("manifest_size_invalid")
    try:
        raw = json.loads(payload)
    except (ValueError, TypeError, RecursionError):
        raise WorkflowError("manifest_json_invalid") from None
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "episode", "fps", "shots"}:
        raise WorkflowError("manifest_schema_invalid")
    if raw["schema_version"] != "arusia.v9.0":
        raise WorkflowError("manifest_version_invalid")
    code = _string(raw["episode"], CODE, "episode_code_invalid")
    if type(raw["fps"]) is not int or raw["fps"] != 24:
        raise WorkflowError("fps_must_be_24")
    shots = raw["shots"]
    if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
        raise WorkflowError("shot_count_invalid")
    seen = set()
    parsed = []
    total = 0
    for shot in shots:
        if not isinstance(shot, dict) or set(shot) != {
            "shot_id", "action", "duration_frames", "characters", "location_id", "asset_ids",
        }:
            raise WorkflowError("shot_schema_invalid")
        shot_id = _string(shot["shot_id"], SHOT, "shot_id_invalid")
        if shot_id in seen:
            raise WorkflowError("duplicate_shot_id")
        seen.add(shot_id)
        action = shot["action"]
        if not isinstance(action, str) or not 1 <= len(action) <= 280 or any(
            ord(char) < 32 for char in action
        ):
            raise WorkflowError("shot_action_invalid")
        frames = shot["duration_frames"]
        if type(frames) is not int or not 1 <= frames <= 3600:
            raise WorkflowError("duration_frames_invalid")
        chars = shot["characters"]
        if not isinstance(chars, list) or not 1 <= len(chars) <= 5 or len(chars) != len(set(
            c for c in chars if isinstance(c, str)
        )) or any(not isinstance(c, str) or c not in CHARACTERS for c in chars):
            raise WorkflowError("characters_invalid")
        location = _string(shot["location_id"], ASSET, "location_id_invalid")
        refs = shot["asset_ids"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
            raise WorkflowError("asset_refs_invalid")
        if (any(not isinstance(ref, str) or not ASSET.fullmatch(ref) for ref in refs)
                or len(set(refs)) != len(refs)):
            raise WorkflowError("asset_refs_invalid")
        total += frames
        parsed.append({"shot_id": shot_id, "action": action,
                       "duration_frames": frames, "characters": chars,
                       "location_id": location, "asset_ids": refs})
    normalized = {"schema_version": "arusia.v9.0", "episode": code, "fps": 24, "shots": parsed}
    compact = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return normalized, hashlib.sha256(compact.encode("utf-8")).hexdigest()


def preview(payload: str) -> dict[str, Any]:
    manifest, checksum = parse_manifest(payload)
    shots = manifest["shots"]
    required = sorted(set().union(*(set(x["asset_ids"]) | {x["location_id"]} |
                                    {name + "_MASTER" for name in x["characters"]} for x in shots)))
    return {
        "status": "PLAN_VALID", "episode": manifest["episode"], "manifest_sha256": checksum,
        "shots": len(shots), "fps": 24, "total_frames": sum(x["duration_frames"] for x in shots),
        "expected_duration_seconds": round(sum(x["duration_frames"] for x in shots) / 24, 3),
        "task_count": len(shots) * len(STAGES) + len(EPISODE_STAGES), "required_asset_ids": required,
        "asset_files_verified": False, "generated_frames": False, "episode_rendered": False,
        "stages": [{"name": stage, "kind": kind, "subsystem": subsystem}
                   for stage, kind, subsystem in (*STAGES, *EPISODE_STAGES)],
    }


@dataclass(frozen=True)
class ArusiaWorkflow:
    vault: Vault
    artifact_root: str | Path | None = None

    def _artifacts(self):
        """Production evidence is checked from bytes on disk, never model claims."""
        if self.artifact_root is None:
            raise WorkflowError("artifact_store_not_configured")
        from im19_mcp.arusia.artifacts import ArtifactRegistry
        return ArtifactRegistry(self.vault, self.artifact_root)

    def create(self, subject: str, manifest_json: str) -> dict[str, Any]:
        manifest, digest = parse_manifest(manifest_json)
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise WorkflowError("verified_mcp_subject_required")
        code = manifest["episode"]
        compact = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        with self.vault.sessions.begin() as session:
            existing = session.execute(select(ArusiaEpisode).where(
                ArusiaEpisode.subject == subject, ArusiaEpisode.code == code,
            )).scalar_one_or_none()
            if existing:
                if existing.manifest_hash != digest:
                    raise WorkflowError("episode_exists_with_different_manifest")
                return {"status": "EXISTING", "episode_id": existing.episode_id,
                        "episode": code, "manifest_sha256": digest}
            eid = secrets.token_hex(16)
            session.add(ArusiaEpisode(episode_id=eid, subject=subject, code=code,
                                      manifest_hash=digest, manifest_json=compact,
                                      created_at=time.time()))
            for shot in manifest["shots"]:
                for position, (stage, kind, _) in enumerate(STAGES):
                    status = ("READY" if position == 0 else "BLOCKED")
                    session.add(ArusiaTask(task_id=secrets.token_hex(16), episode_id=eid,
                                           shot_id=shot["shot_id"], stage=stage,
                                           position=position, kind=kind, status=status))
            for position, (stage, kind, _) in enumerate(EPISODE_STAGES):
                session.add(ArusiaTask(task_id=secrets.token_hex(16), episode_id=eid,
                                       shot_id=EPISODE_KEY, stage=stage,
                                       position=position, kind=kind, status="BLOCKED"))
        return {"status": "CREATED", "episode_id": eid, "episode": code,
                "manifest_sha256": digest, "task_count": len(manifest["shots"]) * len(STAGES) + len(EPISODE_STAGES),
                "external_side_effects": False}

    def inspect(self, subject: str, episode_id: str, *, offset: int = 0,
                limit: int = 50) -> dict[str, Any]:
        _string(episode_id, re.compile("[0-9a-f]{32}"), "episode_id_invalid")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise WorkflowError("pagination_invalid")
        with self.vault.sessions() as session:
            ep = session.execute(select(ArusiaEpisode).where(
                ArusiaEpisode.episode_id == episode_id, ArusiaEpisode.subject == subject,
            )).scalar_one_or_none()
            if ep is None:
                raise WorkflowError("episode_not_found")
            all_tasks = session.execute(select(ArusiaTask).where(
                ArusiaTask.episode_id == episode_id,
            ).order_by(ArusiaTask.shot_id, ArusiaTask.position)).scalars().all()
            subset = all_tasks[offset:offset + limit]
            return {
                "status": "OK", "episode_id": ep.episode_id, "episode": ep.code,
                "manifest_sha256": ep.manifest_hash, "total_tasks": len(all_tasks),
                "offset": offset, "next_offset": offset + limit if offset + limit < len(all_tasks) else None,
                "counts": {name: sum(1 for task in all_tasks if task.status == name)
                           for name in ("BLOCKED", "READY", "LEASED", "DONE",
                                        "WAITING_APPROVAL", "APPROVED", "FAILED")},
                "tasks": [{"task_id": x.task_id, "shot_id": x.shot_id, "stage": x.stage,
                           "kind": x.kind, "status": x.status, "attempts": x.attempts,
                           "worker_reported_output": bool(x.output_json != "{}"),
                           "human_evidence_recorded": bool(x.evidence_digest)}
                          for x in subset],
                "episode_rendered": False, "asset_files_verified": False,
                "episode_assembly_requires_all_shots": True,
                "note": "Task DONE is a worker report, not an independently verified media artifact.",
            }

    def _next(self, session: Any, task: ArusiaTask) -> None:
        """Release the successor only after direct dependencies have completed.

        Unlike per-shot stages, the episode composite is a fan-in barrier:
        every shot must pass SHOT_QC before it can be claimed.
        """
        successor = session.execute(select(ArusiaTask).where(
            ArusiaTask.episode_id == task.episode_id,
            ArusiaTask.shot_id == task.shot_id,
            ArusiaTask.position == task.position + 1,
        )).scalar_one_or_none()
        if successor is not None and successor.status == "BLOCKED":
            successor.status = "WAITING_APPROVAL" if successor.kind == "HUMAN" else "READY"
        if task.shot_id != EPISODE_KEY and task.stage == "SHOT_QC":
            terminal = session.execute(select(ArusiaTask).where(
                ArusiaTask.episode_id == task.episode_id,
                ArusiaTask.stage == "SHOT_QC",
            )).scalars().all()
            if terminal and all(row.status == "APPROVED" for row in terminal):
                assembly = session.execute(select(ArusiaTask).where(
                    ArusiaTask.episode_id == task.episode_id,
                    ArusiaTask.shot_id == EPISODE_KEY,
                    ArusiaTask.stage == "EPISODE_COMPOSITE",
                )).scalar_one_or_none()
                if assembly is not None and assembly.status == "BLOCKED":
                    assembly.status = "READY"

    def claim(self, subject: str, task_id: str, *, worker_id: str) -> dict[str, Any]:
        """Trusted worker CLI only. Concurrent claims use conditional SQL UPDATE."""
        _string(task_id, re.compile("[0-9a-f]{32}"), "task_id_invalid")
        _string(worker_id, re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,63}"), "worker_id_invalid")
        with self.vault.sessions.begin() as session:
            row = session.execute(select(ArusiaTask, ArusiaEpisode.subject).join(
                ArusiaEpisode, ArusiaTask.episode_id == ArusiaEpisode.episode_id
            ).where(ArusiaTask.task_id == task_id, ArusiaEpisode.subject == subject)).one_or_none()
            if row is None:
                raise WorkflowError("task_not_found")
            task, _ = row
            if task.kind != "WORKER" or task.attempts >= 3:
                raise WorkflowError("task_not_claimable")
            token = secrets.token_urlsafe(32)
            now = time.time()
            query = update(ArusiaTask).where(
                ArusiaTask.task_id == task_id,
                ArusiaTask.attempts < 3,
                ((ArusiaTask.status == "READY") |
                 ((ArusiaTask.status == "LEASED") & (ArusiaTask.lease_until <= now))),
            ).values(status="LEASED", attempts=ArusiaTask.attempts + 1,
                     lease_digest=hashlib.sha256(token.encode()).hexdigest(),
                     lease_until=now + 600)
            if session.execute(query).rowcount != 1:
                raise WorkflowError("task_not_claimable")
            return {"status": "LEASED", "task_id": task_id, "worker_id": worker_id,
                    "lease_token": token, "lease_seconds": 600, "stage": task.stage,
                    "shot_id": task.shot_id, "episode_id": task.episode_id}

    def complete(self, subject: str, task_id: str, lease_token: str,
                 artifact_sha256: str, *, provenance: dict[str, Any]) -> dict[str, Any]:
        """Trusted worker reports digest; final evidence approval remains independent."""
        _string(task_id, re.compile("[0-9a-f]{32}"), "task_id_invalid")
        _string(artifact_sha256, DIGEST, "artifact_digest_invalid")
        if not isinstance(lease_token, str) or not 30 <= len(lease_token) <= 128:
            raise WorkflowError("lease_token_invalid")
        if not isinstance(provenance, dict) or len(json.dumps(provenance)) > 4096:
            raise WorkflowError("provenance_invalid")
        # Only immutable blobs actually ingested for this task are admissible.
        self._artifacts().check(
            subject, "TASK_" + task_id.upper(), artifact_sha256, approved=False,
        )
        # Deterministic image runs must declare model, references, resolution and seed.
        stage = ""
        with self.vault.sessions.begin() as session:
            row = session.execute(select(ArusiaTask, ArusiaEpisode.subject).join(
                ArusiaEpisode, ArusiaTask.episode_id == ArusiaEpisode.episode_id
            ).where(ArusiaTask.task_id == task_id, ArusiaEpisode.subject == subject)).one_or_none()
            if row is None:
                raise WorkflowError("task_not_found")
            task, _ = row
            stage = task.stage
            if stage == "IMAGE_GENERATION":
                if (set(provenance) != {"model_sha256", "lora_sha256", "controlnet_sha256",
                                      "reference_sha256", "seed", "resolution"} or
                        any(not isinstance(provenance.get(key), str) or
                            not DIGEST.fullmatch(provenance[key])
                            for key in ("model_sha256", "lora_sha256", "controlnet_sha256")) or
                        not isinstance(provenance.get("reference_sha256"), list) or
                        not provenance["reference_sha256"] or
                        any(not isinstance(v, str) or not DIGEST.fullmatch(v)
                            for v in provenance["reference_sha256"]) or
                        type(provenance.get("seed")) is not int or
                        not 0 <= provenance["seed"] <= 2**32 - 1 or
                        provenance.get("resolution") not in ("1024x1024", "1024x576", "1920x1080")):
                    raise WorkflowError("image_generation_provenance_required")
            digest = hashlib.sha256(lease_token.encode()).hexdigest()
            now = time.time()
            q = update(ArusiaTask).where(
                ArusiaTask.task_id == task_id, ArusiaTask.status == "LEASED",
                ArusiaTask.lease_digest == digest, ArusiaTask.lease_until > now,
            ).values(status="DONE", lease_digest="", lease_until=0.0,
                     output_json=json.dumps({"artifact_sha256": artifact_sha256,
                                             "provenance": provenance}, sort_keys=True, allow_nan=False))
            if session.execute(q).rowcount != 1:
                raise WorkflowError("lease_invalid_or_expired")
            self._next(session, task)
        return {"status": "WORKER_REPORTED_DONE", "task_id": task_id,
                "artifact_sha256": artifact_sha256, "independent_qc_verified": False}

    def approve(self, subject: str, task_id: str, *, evidence_sha256: str) -> dict[str, Any]:
        """Trusted operator CLI only: never expose as MCP approval tool."""
        _string(task_id, re.compile("[0-9a-f]{32}"), "task_id_invalid")
        _string(evidence_sha256, DIGEST, "human_evidence_digest_invalid")
        with self.vault.sessions.begin() as session:
            row = session.execute(select(ArusiaTask, ArusiaEpisode.subject).join(
                ArusiaEpisode, ArusiaTask.episode_id == ArusiaEpisode.episode_id
            ).where(ArusiaTask.task_id == task_id, ArusiaEpisode.subject == subject)).one_or_none()
            if row is None:
                raise WorkflowError("task_not_found")
            task, _ = row
            if task.kind != "HUMAN" or task.status != "WAITING_APPROVAL":
                raise WorkflowError("human_gate_not_ready")
            # Even human acceptance must reference evidence bytes in the registry.
            registry = self._artifacts()
            registry.check(subject, "EVIDENCE_" + task_id.upper(),
                           evidence_sha256, approved=False)
            if task.stage == "ASSET_LOCK":
                episode = session.execute(select(ArusiaEpisode).where(
                    ArusiaEpisode.episode_id == task.episode_id,
                    ArusiaEpisode.subject == subject,
                )).scalar_one()
                shot = next((entry for entry in json.loads(episode.manifest_json)["shots"]
                             if entry["shot_id"] == task.shot_id), None)
                if shot is None:
                    raise WorkflowError("shot_not_found")
                required = (set(shot["asset_ids"]) | {shot["location_id"]} |
                            {character + "_MASTER" for character in shot["characters"]})
                for asset_id in required:
                    registry.check(subject, asset_id, approved=True)
            if task.stage == "SHOT_QC":
                # Serialize concurrent final-shot approvals via an episode row
                # lock. Without this, simultaneous commits can both miss the
                # fan-in condition and leave EPISODE_COMPOSITE blocked forever.
                session.execute(select(ArusiaEpisode).where(
                    ArusiaEpisode.episode_id == task.episode_id,
                ).with_for_update()).scalar_one()
            changed = session.execute(update(ArusiaTask).where(
                ArusiaTask.task_id == task_id, ArusiaTask.status == "WAITING_APPROVAL"
            ).values(status="APPROVED", evidence_digest=evidence_sha256))
            if changed.rowcount != 1:
                raise WorkflowError("human_gate_not_ready")
            self._next(session, task)
        return {"status": "APPROVED", "task_id": task_id,
                "evidence_sha256": evidence_sha256, "approver": "trusted_operator_cli"}
