"""ARUSIA v9 tests: no actual ComfyUI, Blender or FFmpeg invoked."""
from dataclasses import replace
import hashlib
import json
import time

import pytest
from cryptography.fernet import Fernet
from mcp import Client
from mcp.server import MCPServer
from sqlalchemy import select, update

from im19_mcp.arusia.workflow import (
    ArusiaTask, ArusiaWorkflow, WorkflowError, STAGES, parse_manifest, preview,
)
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
    yield ArusiaWorkflow(store)
    store.engine.dispose()


def find_task(workflow, subject, episode_id, stage):
    return next(row for row in workflow.inspect(subject, episode_id, limit=100)["tasks"]
                if row["stage"] == stage)


def test_manifest_preflight_is_bounded_and_24fps():
    plan = preview(sample())
    assert plan["total_frames"] == 120
    assert plan["expected_duration_seconds"] == 5
    assert plan["task_count"] == len(STAGES)
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
    assert one["task_count"] == 12
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
    with pytest.raises(WorkflowError, match="lease_invalid_or_expired"):
        workflow.complete("alice", first["task_id"], "wrong", FAKE_SHA, provenance={})
    done = workflow.complete("alice", first["task_id"], info["lease_token"],
                             FAKE_SHA, provenance={"worker_version": "mock-v1"})
    assert done["status"] == "WORKER_REPORTED_DONE"
    assert not done["independent_qc_verified"]
    assert find_task(workflow, "alice", eid, "ASSET_LOCK")["status"] == "WAITING_APPROVAL"
    with pytest.raises(WorkflowError):
        workflow.approve("bob", find_task(workflow, "alice", eid, "ASSET_LOCK")["task_id"],
                         evidence_sha256=FAKE_SHA)
    gate = workflow.approve("alice", find_task(workflow, "alice", eid, "ASSET_LOCK")["task_id"],
                            evidence_sha256=FAKE_SHA)
    assert gate["status"] == "APPROVED"
    with pytest.raises(WorkflowError, match="human_gate_not_ready"):
        workflow.approve("alice", find_task(workflow, "alice", eid, "ASSET_LOCK")["task_id"],
                         evidence_sha256=FAKE_SHA)
    assert find_task(workflow, "alice", eid, "STORYBOARD")["status"] == "READY"


def test_lease_expiration_retry_cap_and_fails_closed(workflow):
    eid = workflow.create("alice", sample())["episode_id"]
    task = find_task(workflow, "alice", eid, "SCRIPT_BREAKDOWN")
    for i in range(3):
        attempt = workflow.claim("alice", task["task_id"], worker_id="local-worker")
        with workflow.vault.sessions.begin() as session:
            session.execute(update(ArusiaTask).where(
                ArusiaTask.task_id == task["task_id"]
            ).values(lease_until=time.time() - 1))
        with pytest.raises(WorkflowError, match="lease_invalid_or_expired"):
            workflow.complete("alice", task["task_id"], attempt["lease_token"], FAKE_SHA, provenance={})
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
    with pytest.raises(WorkflowError, match="image_generation_provenance_required"):
        workflow.complete("alice", task["task_id"], info["lease_token"], FAKE_SHA, provenance={})
    good = {
        "model_sha256": FAKE_SHA, "lora_sha256": FAKE_SHA,
        "controlnet_sha256": OTHER_SHA,
        "reference_sha256": [OTHER_SHA],
        "seed": 123456, "resolution": "1024x576",
    }
    response = workflow.complete("alice", task["task_id"], info["lease_token"],
                                 FAKE_SHA, provenance=good)
    assert response["status"] == "WORKER_REPORTED_DONE"
    assert find_task(workflow, "alice", eid, "FRAME_QC")["status"] == "WAITING_APPROVAL"


@pytest.mark.asyncio
async def test_mcp_plan_and_episode_create_require_subject_and_feature_flag(workflow, monkeypatch):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "alice")
    settings = replace(Settings.from_env(), enable_arusia_workflows=True)
    server = MCPServer("ARUSIA v9 staging")
    register_tools(server, settings=settings, vault=workflow.vault, oauth=None,
                   allow_stdio_dev=True)
    async with Client(server) as client:
        names = {x.name for x in (await client.list_tools()).tools}
        assert {"arusia_plan_episode", "arusia_create_episode", "arusia_get_episode"} <= names
        plan = await client.call_tool("arusia_plan_episode", {"manifest_json": sample()})
        assert plan.structured_content["status"] == "PLAN_VALID"
        create = await client.call_tool("arusia_create_episode", {"manifest_json": sample()})
        data = create.structured_content
        assert data["status"] == "CREATED"
        read = await client.call_tool("arusia_get_episode", {"episode_id": data["episode_id"]})
        assert read.structured_content["total_tasks"] == 12
        assert not read.structured_content["episode_rendered"]
    server = MCPServer("ARUSIA disabled")
    register_tools(server, settings=replace(settings, enable_arusia_workflows=False),
                   vault=workflow.vault, oauth=None, allow_stdio_dev=True)
    async with Client(server) as client:
        status = await client.call_tool("arusia_create_episode", {"manifest_json": sample(code="S01E02")})
        assert status.structured_content["status"] == "FEATURE_DISABLED"
