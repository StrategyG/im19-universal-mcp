"""Deterministic autonomous workflow, CAS leases, isolation and fail-closed MCP."""
from dataclasses import replace
import json
import time

import pytest
from cryptography.fernet import Fernet
from mcp import Client
from mcp.server import MCPServer
from sqlalchemy import select, update

from im19_mcp.arusia.workflow import ArusiaEpisode
from im19_mcp.autonomy.engine import (
    AutonomousEngine, AutonomousError, WorkflowStep,
    compile_plan, prepare_plan,
)
from im19_mcp.config import Settings
from im19_mcp.server import register_tools
from im19_mcp.storage import Vault


def manifest(*, episode="S01E01", frames=120):
    return json.dumps({
        "schema_version": "arusia.v9.0", "episode": episode, "fps": 24,
        "shots": [{
            "shot_id": "SH001", "action": "Kio mengangkat bintang.",
            "duration_frames": frames, "characters": ["KIO"],
            "location_id": "HALAMAN_KIO",
            "asset_ids": ["KIO_MASTER", "HALAMAN_KIO_BG", "BINTANG_PROP"],
        }],
    })


@pytest.fixture
def engine(tmp_path):
    vault = Vault("sqlite:///" + str(tmp_path / "autonomy.sqlite"),
                  encryption_key=Fernet.generate_key().decode(), audit_salt="synthetic-audit")
    vault.initialize_schema()
    yield AutonomousEngine(vault)
    vault.engine.dispose()


def test_planner_is_pure_and_compiler_rejects_arbitrary_tasks():
    plan = prepare_plan("siapkan episode arusia", manifest())
    assert plan["status"] == "PLAN_VALID"
    assert plan["steps"] == 2
    assert plan["external_side_effects"] is False
    parsed, digest = compile_plan(plan["plan_json"])
    assert digest == plan["plan_sha256"]
    assert parsed["nodes"][0]["operation"] == "arusia.manifest_preview"
    assert parsed["nodes"][1]["depends_on"] == ["inspect_manifest"]
    with pytest.raises(AutonomousError, match="unsupported_goal"):
        prepare_plan("buka shell", manifest())
    for changed in [
        ("operation", "subprocess.run"),
        ("depends_on", []),
        ("id", "bad_node"),
        ("arguments", {"url": "https://untrusted.example"}),
    ]:
        body = json.loads(plan["plan_json"])
        body["nodes"][1][changed[0]] = changed[1]
        with pytest.raises(AutonomousError):
            compile_plan(json.dumps(body))
    body = json.loads(plan["plan_json"])
    body["nodes"].append({"id": "danger", "operation": "shell.exec",
                          "depends_on": [], "arguments": {"command": "rm -rf /"}})
    with pytest.raises(AutonomousError):
        compile_plan(json.dumps(body))


def test_draft_requires_explicit_owner_start_and_executes_two_metadata_steps(engine):
    planned = prepare_plan("prepare_arusia_episode", manifest())
    new = engine.create("alice", planned["plan_json"])
    assert new["status"] == "CREATED"
    rid = new["run_id"]
    assert engine.create("alice", planned["plan_json"])["status"] == "EXISTING"
    assert engine.status("alice", rid)["status"] == "DRAFT"
    with pytest.raises(AutonomousError, match="workflow_not_found"):
        engine.status("mallory", rid)
    with pytest.raises(AutonomousError, match="workflow_not_found"):
        engine.action("mallory", rid, "start")
    assert engine.run_once("alice", rid)["status"] == "DRAFT"
    assert engine.action("alice", rid, "start")["status"] == "RUNNING"
    with pytest.raises(AutonomousError, match="transition_denied"):
        engine.action("alice", rid, "start")
    first = engine.run_once("alice", rid)
    assert first["status"] == "DONE"
    assert first["step"] == "inspect_manifest"
    assert engine.status("alice", rid)["status"] == "RUNNING"
    second = engine.run_once("alice", rid)
    assert second["status"] == "DONE"
    assert second["step"] == "register_episode"
    assert second["workflow_status"] == "COMPLETE"
    state = engine.status("alice", rid)
    assert [s["status"] for s in state["steps"]] == ["DONE", "DONE"]
    assert state["media_generated"] is False
    assert state["episode_render_verified"] is False
    assert "lease_token" not in json.dumps(state)
    with engine.vault.sessions() as session:
        rows = session.execute(select(ArusiaEpisode).where(
            ArusiaEpisode.subject == "alice",
        )).scalars().all()
    assert len(rows) == 1
    assert engine.run_once("alice", rid)["status"] == "COMPLETE"


def test_pause_resume_cancel_and_blocked_worker_recovery(engine):
    plan = prepare_plan("prepare_arusia_episode", manifest())
    rid = engine.create("alice", plan["plan_json"])["run_id"]
    engine.action("alice", rid, "start")
    lease = engine._claim("alice", rid)
    assert lease["operation"] == "arusia.manifest_preview"
    engine.action("alice", rid, "pause")
    assert engine.run_once("alice", rid)["status"] == "PAUSED"
    assert engine._finish("alice", lease, {"status": "FAKE"})["status"] == "WORKFLOW_NOT_RUNNING"
    with pytest.raises(AutonomousError):
        engine.action("alice", rid, "start")
    engine.action("alice", rid, "resume")
    # Simulate crash while lease was held; the original token must be stale.
    with engine.vault.sessions.begin() as session:
        session.execute(update(WorkflowStep).where(
            WorkflowStep.step_id == lease["step_id"],
        ).values(lease_until=time.time() - 5))
    assert engine.run_once("alice", rid)["status"] == "DONE"
    assert engine._finish("alice", lease, {"status": "FAKE"})["status"] == "LEASE_EXPIRED_OR_INVALID"
    assert engine.status("alice", rid)["steps"][0]["attempts"] == 2
    engine.action("alice", rid, "cancel")
    assert engine.run_once("alice", rid)["status"] == "CANCELLED"
    assert engine.status("alice", rid)["status"] == "CANCELLED"
    with pytest.raises(AutonomousError, match="transition_denied"):
        engine.action("alice", rid, "resume")


def test_exhausted_worker_lease_terminally_fails(engine):
    rid = engine.create("alice", prepare_plan("prepare_arusia_episode", manifest())["plan_json"])["run_id"]
    engine.action("alice", rid, "start")
    for index in range(3):
        lease = engine._claim("alice", rid)
        assert lease is not None
        with engine.vault.sessions.begin() as session:
            session.execute(update(WorkflowStep).where(
                WorkflowStep.step_id == lease["step_id"],
            ).values(lease_until=time.time() - 2))
    response = engine.run_once("alice", rid)
    assert response["status"] == "FAILED"
    assert engine.status("alice", rid)["status"] == "FAILED"


def test_plan_replay_is_owner_scoped(engine):
    plan = prepare_plan("prepare_arusia_episode", manifest())
    alice = engine.create("alice", plan["plan_json"])
    bob = engine.create("bob", plan["plan_json"])
    assert alice["run_id"] != bob["run_id"]
    assert engine.status("bob", bob["run_id"])["status"] == "DRAFT"
    with pytest.raises(AutonomousError, match="workflow_not_found"):
        engine.status("alice", bob["run_id"])


@pytest.mark.asyncio
async def test_mcp_tool_surface_and_disabled_by_default(engine, monkeypatch):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "alice")
    enabled = replace(Settings.from_env(), enable_arusia_workflows=True,
                      enable_autonomous_workflows=True)
    server = MCPServer("autonomy staging")
    register_tools(server, settings=enabled, vault=engine.vault, oauth=None,
                   allow_stdio_dev=True)

    def unpack(result):
        return result.structured_content or json.loads(result.content[0].text)

    async with Client(server) as client:
        toolset = {x.name for x in (await client.list_tools()).tools}
        assert {"im19_workflow_plan", "im19_workflow_validate", "im19_workflow_create",
                "im19_workflow_start", "im19_workflow_status", "im19_workflow_pause",
                "im19_workflow_resume", "im19_workflow_cancel"} <= toolset
        assert "im19_workflow_run_once" not in toolset
        plan = unpack(await client.call_tool(
            "im19_workflow_plan", {"goal": "prepare_arusia_episode",
                                   "manifest_json": manifest()}))
        assert plan["status"] == "PLAN_VALID"
        validated = unpack(await client.call_tool(
            "im19_workflow_validate", {"plan_json": plan["plan_json"]}))
        assert validated["status"] == "PLAN_VALID"
        result = unpack(await client.call_tool(
            "im19_workflow_create", {"plan_json": plan["plan_json"]}))
        assert result["status"] == "CREATED"
        run_id = result["run_id"]
        assert unpack(await client.call_tool(
            "im19_workflow_start", {"run_id": run_id}))["status"] == "RUNNING"
        assert unpack(await client.call_tool(
            "im19_workflow_status", {"run_id": run_id}))["status"] == "RUNNING"
        assert unpack(await client.call_tool(
            "im19_workflow_pause", {"run_id": run_id}))["status"] == "PAUSED"
        assert unpack(await client.call_tool(
            "im19_workflow_resume", {"run_id": run_id}))["status"] == "RUNNING"
        assert unpack(await client.call_tool(
            "im19_workflow_cancel", {"run_id": run_id}))["status"] == "CANCELLED"
    server2 = MCPServer("autonomy disabled")
    register_tools(server2, settings=replace(enabled, enable_autonomous_workflows=False),
                   vault=engine.vault, oauth=None, allow_stdio_dev=True)
    async with Client(server2) as client:
        denied = unpack(await client.call_tool(
            "im19_workflow_create",
            {"plan_json": prepare_plan("prepare_arusia_episode", manifest(episode="S01E02"))["plan_json"]}))
        assert denied["status"] == "FEATURE_DISABLED"
