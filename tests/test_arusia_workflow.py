"""ARUSIA v9 tests: no actual ComfyUI, Blender or FFmpeg invoked."""
from dataclasses import replace
import json
import time
import secrets

import pytest
from cryptography.fernet import Fernet
from mcp import Client
from mcp.server import MCPServer
from sqlalchemy import select, update

from im19_mcp.arusia.workflow import (
    ArusiaTask, ArusiaWorkflow, WorkflowError, STAGES, EPISODE_STAGES, parse_manifest, preview,
)
from im19_mcp.arusia.artifacts import ArtifactRegistry
from im19_mcp.config import Settings
from im19_mcp.server import register_tools
from im19_mcp.storage import Vault

FAKE_SHA = "a" * 64
OTHER_SHA = "b" * 64


def sample(code="S01E01", shot="SH001", duration=120):
    return json.dumps({"schema_version": "arusia.v9.0", "episode": code, "fps": 24, "shots": [{
        "shot_id": shot, "action": "Kio mengangkat bintang.",
        "duration_frames": duration, "characters": ["KIO"],
        "location_id": "HALAMAN_KIO", "asset_ids": ["KIO_MASTER", "HALAMAN_KIO_BG", "BINTANG_PROP"],
    }]})


@pytest.fixture
def workflow(tmp_path):
    store = Vault("sqlite:///" + str(tmp_path / "arusia.sqlite"),
                  encryption_key=Fernet.generate_key().decode(), audit_salt="arusia-test")
    store.initialize_schema()
    root = tmp_path / "artifact_store"
    root.mkdir()
    yield ArusiaWorkflow(store, root)
    store.engine.dispose()


def file_evidence(workflow, subject, asset_id, *, label="verified"):
    registry = ArtifactRegistry(workflow.vault, workflow.artifact_root)
    name = secrets.token_hex(8) + ".json"
    (registry.incoming / name).write_text(json.dumps({"label": label, "asset_id": asset_id}))
    return registry.ingest(subject, asset_id, name)["sha256"]


def evidence_for_task(workflow, subject, task_id):
    return file_evidence(workflow, subject, "EVIDENCE_" + task_id.upper())


def output_for_task(workflow, subject, task_id):
    return file_evidence(workflow, subject, "TASK_" + task_id.upper())


def install_masters(workflow, subject):
    registry = ArtifactRegistry(workflow.vault, workflow.artifact_root)
    for asset in ("KIO_MASTER", "HALAMAN_KIO", "HALAMAN_KIO_BG", "BINTANG_PROP"):
        master = file_evidence(workflow, subject, asset)
        proof = file_evidence(workflow, subject, "EVIDENCE_" + asset)
        registry.approve(subject, asset, master, proof)


def find_task(workflow, subject, episode_id, stage):
    return next(row for row in workflow.inspect(subject, episode_id, limit=100)["tasks"]
                if row["stage"] == stage)


def test_manifest_preflight_is_bounded_and_24fps():
    plan = preview(sample())
    assert plan["total_frames"] == 120
    assert plan["expected_duration_seconds"] == 5
    assert plan["task_count"] == len(STAGES) + len(EPISODE_STAGES)
    assert plan["required_asset_ids"] == [
        "BINTANG_PROP", "HALAMAN_KIO", "HALAMAN_KIO_BG", "KIO_MASTER",
    ]
    assert plan["asset_files_verified"] is False
    assert plan["episode_rendered"] is False


@pytest.mark.parametrize("invalid", [
    '{"schema_version":"arusia.v9.0","episode":"S01E01","fps":24,"shots":[]}',
    sample().replace('"fps": 24', '"fps": true'),
    sample().replace('"fps": 24', '"fps": 30'),
    sample().replace('"duration_frames": 120', '"duration_frames": 0'),
    sample().replace('"KIO"', '"DUPLICATE_CHAR"'),
    sample().replace('"SH001"', '"../../bad"'),
    sample().replace('"S01E01"', '"bad"'),
    sample() + " trailing garbage",
    '{"schema_version":"arusia.v9.0","episode":"S01E01","fps":24,"shots":null}',
])
def test_manifest_malformed_denied(invalid):
    with pytest.raises(WorkflowError):
        parse_manifest(invalid)


def test_same_episode_is_idempotent_and_cross_owner_hidden(workflow):
    p = sample()
    one = workflow.create("alice", p)
    again = workflow.create("alice", p)
    assert one["status"] == "CREATED"
    assert again["status"] == "EXISTING"
    assert again["episode_id"] == one["episode_id"]
    assert one["task_count"] == len(STAGES) + len(EPISODE_STAGES)
    with pytest.raises(WorkflowError, match="different_manifest"):
        workflow.create("alice", sample(duration=121))
    with pytest.raises(WorkflowError, match="episode_not_found"):
        workflow.inspect("bob", one["episode_id"])
    different = workflow.create("bob", p)
    assert different["episode_id"] != one["episode_id"]


def test_manual_gate_prevents_automatic_generation_and_reuse(workflow):
    eid = workflow.create("alice", sample())["episode_id"]
    stage = "SCRIPT_BREAKDOWN"
    first = find_task(workflow, "alice", eid, stage)
    with pytest.raises(WorkflowError, match="human_gate_not_ready"):
        workflow.approve("alice", first["task_id"], evidence_sha256=FAKE_SHA)
    other = find_task(workflow, "alice", eid, "IMAGE_GENERATION")
    with pytest.raises(WorkflowError, match="task_not_claimable"):
        workflow.claim("alice", other["task_id"], worker_id="worker-001")
    info = workflow.claim("alice", first["task_id"], worker_id="worker-001")
    assert info["status"] == "LEASED"
    with pytest.raises(WorkflowError, match="task_not_claimable"):
        workflow.claim("alice", first["task_id"], worker_id="worker-002")
    verified = output_for_task(workflow, "alice", first["task_id"])
    with pytest.raises(WorkflowError, match="lease_invalid_or_expired"):
        workflow.complete("alice", first["task_id"], "x" * 43, verified, provenance={})
    done = workflow.complete("alice", first["task_id"], info["lease_token"],
                             verified, provenance={"worker_version": "mock-v1"})
    assert done["status"] == "WORKER_REPORTED_DONE"
    assert not done["independent_qc_verified"]
    assert find_task(workflow, "alice", eid, "ASSET_LOCK")["status"] == "WAITING_APPROVAL"
    with pytest.raises(WorkflowError):
        workflow.approve("bob", find_task(workflow, "alice", eid, "ASSET_LOCK")["task_id"],
                         evidence_sha256=FAKE_SHA)
    install_masters(workflow, "alice")
    gate_id = find_task(workflow, "alice", eid, "ASSET_LOCK")["task_id"]
    gate_proof = evidence_for_task(workflow, "alice", gate_id)
    gate = workflow.approve("alice", gate_id, evidence_sha256=gate_proof)
    assert gate["status"] == "APPROVED"
    with pytest.raises(WorkflowError, match="human_gate_not_ready"):
        workflow.approve("alice", find_task(workflow, "alice", eid, "ASSET_LOCK")["task_id"],
                         evidence_sha256=gate_proof)
    assert find_task(workflow, "alice", eid, "STORYBOARD")["status"] == "READY"


def test_lease_expiration_retry_cap_and_fails_closed(workflow):
    eid = workflow.create("alice", sample())["episode_id"]
    task = find_task(workflow, "alice", eid, "SCRIPT_BREAKDOWN")
    verified = output_for_task(workflow, "alice", task["task_id"])
    for i in range(3):
        attempt = workflow.claim("alice", task["task_id"], worker_id="local-worker")
        with workflow.vault.sessions.begin() as session:
            session.execute(update(ArusiaTask).where(
                ArusiaTask.task_id == task["task_id"]
            ).values(lease_until=time.time() - 1))
        with pytest.raises(WorkflowError, match="lease_invalid_or_expired"):
            workflow.complete("alice", task["task_id"], attempt["lease_token"], verified, provenance={})
        if i == 2:
            with pytest.raises(WorkflowError, match="task_not_claimable"):
                workflow.claim("alice", task["task_id"], worker_id="local-worker")


def test_image_provenance_is_mandatory_before_worker_submission(workflow):
    eid = workflow.create("alice", sample())["episode_id"]
    with workflow.vault.sessions.begin() as session:
        target = session.execute(select(ArusiaTask).where(
            ArusiaTask.episode_id == eid, ArusiaTask.stage == "IMAGE_GENERATION"
        )).scalar_one()
        target.status = "READY"
    task = find_task(workflow, "alice", eid, "IMAGE_GENERATION")
    info = workflow.claim("alice", task["task_id"], worker_id="test-worker")
    verified = output_for_task(workflow, "alice", task["task_id"])
    with pytest.raises(WorkflowError, match="image_generation_provenance_required"):
        workflow.complete("alice", task["task_id"], info["lease_token"], verified, provenance={})
    good = {
        "model_sha256": FAKE_SHA, "lora_sha256": FAKE_SHA,
        "controlnet_sha256": OTHER_SHA,
        "reference_sha256": [OTHER_SHA],
        "seed": 123456, "resolution": "1024x576",
    }
    response = workflow.complete("alice", task["task_id"], info["lease_token"],
                                 verified, provenance=good)
    assert response["status"] == "WORKER_REPORTED_DONE"
    assert find_task(workflow, "alice", eid, "FRAME_QC")["status"] == "WAITING_APPROVAL"


@pytest.mark.asyncio
async def test_mcp_plan_and_episode_create_require_subject_and_feature_flag(workflow, monkeypatch):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "alice")
    settings = replace(Settings.from_env(), enable_arusia_workflows=True)
    server = MCPServer("ARUSIA v9 staging")
    register_tools(server, settings=settings, vault=workflow.vault, oauth=None,
                   allow_stdio_dev=True)
    def body(result):
        return (result.structured_content if result.structured_content is not None
                else json.loads(result.content[0].text))

    async with Client(server) as client:
        names = {x.name for x in (await client.list_tools()).tools}
        assert {"arusia_plan_episode", "arusia_create_episode", "arusia_get_episode"} <= names
        plan = await client.call_tool("arusia_plan_episode", {"manifest_json": sample()})
        assert body(plan)["status"] == "PLAN_VALID"
        create = await client.call_tool("arusia_create_episode", {"manifest_json": sample()})
        data = body(create)
        assert data["status"] == "CREATED"
        read = await client.call_tool("arusia_get_episode", {"episode_id": data["episode_id"]})
        assert body(read)["total_tasks"] == len(STAGES) + len(EPISODE_STAGES)
        assert not body(read)["episode_rendered"]
    server = MCPServer("ARUSIA disabled")
    register_tools(server, settings=replace(settings, enable_arusia_workflows=False),
                   vault=workflow.vault, oauth=None, allow_stdio_dev=True)
    async with Client(server) as client:
        status = await client.call_tool("arusia_create_episode", {"manifest_json": sample(code="S01E02")})
        assert body(status)["status"] == "FEATURE_DISABLED"


def test_full_episode_barrier_waits_for_all_shot_quality_gates(workflow):
    original = json.loads(sample())
    second = dict(original["shots"][0])
    second["shot_id"] = "SH002"
    original["shots"].append(second)
    episode_id = workflow.create("alice", json.dumps(original))["episode_id"]
    assert workflow.inspect("alice", episode_id)["total_tasks"] == 2 * len(STAGES) + len(EPISODE_STAGES)
    composite = find_task(workflow, "alice", episode_id, "EPISODE_COMPOSITE")
    assert composite["status"] == "BLOCKED"
    with pytest.raises(WorkflowError, match="task_not_claimable"):
        workflow.claim("alice", composite["task_id"], worker_id="rig-worker-1")

    with workflow.vault.sessions.begin() as session:
        for shot in ("SH001", "SH002"):
            session.execute(update(ArusiaTask).where(
                ArusiaTask.episode_id == episode_id,
                ArusiaTask.shot_id == shot,
                ArusiaTask.stage == "SHOT_QC",
            ).values(status="WAITING_APPROVAL"))
    qc_one = next(t for t in workflow.inspect("alice", episode_id, limit=100)["tasks"]
                  if t["shot_id"] == "SH001" and t["stage"] == "SHOT_QC")
    qc_two = next(t for t in workflow.inspect("alice", episode_id, limit=100)["tasks"]
                  if t["shot_id"] == "SH002" and t["stage"] == "SHOT_QC")
    first_proof = evidence_for_task(workflow, "alice", qc_one["task_id"])
    second_proof = evidence_for_task(workflow, "alice", qc_two["task_id"])
    assert workflow.approve("alice", qc_one["task_id"], evidence_sha256=first_proof)["status"] == "APPROVED"
    assert find_task(workflow, "alice", episode_id, "EPISODE_COMPOSITE")["status"] == "BLOCKED"
    assert workflow.approve("alice", qc_two["task_id"], evidence_sha256=second_proof)["status"] == "APPROVED"
    assert find_task(workflow, "alice", episode_id, "EPISODE_COMPOSITE")["status"] == "READY"
    lease = workflow.claim("alice", composite["task_id"], worker_id="rig-worker-1")
    assert lease["status"] == "LEASED"
    composite_output = output_for_task(workflow, "alice", composite["task_id"])
    workflow.complete("alice", composite["task_id"], lease["lease_token"], composite_output,
                      provenance={"renderer": "synthetic"})
    assert find_task(workflow, "alice", episode_id, "EPISODE_FINAL_QC")["status"] == "WAITING_APPROVAL"
    final = find_task(workflow, "alice", episode_id, "EPISODE_FINAL_QC")
    final_proof = evidence_for_task(workflow, "alice", final["task_id"])
    workflow.approve("alice", final["task_id"], evidence_sha256=final_proof)
    assert find_task(workflow, "alice", episode_id, "DELIVERY_PACKAGE")["status"] == "READY"
