"""Real PostgreSQL CAS lease acceptance with an ephemeral CI-only database."""
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from im19_mcp.autonomy.engine import AutonomousEngine, WorkflowStep, prepare_plan
from im19_mcp.storage import Vault

pytestmark = pytest.mark.skipif(
    not os.getenv("IM19_INTEGRATION_DB_URL"),
    reason="Requires an isolated PostgreSQL integration database",
)


def _manifest(episode):
    return json.dumps({
        "schema_version": "arusia.v9.0", "episode": episode, "fps": 24,
        "shots": [{
            "shot_id": "SH001", "action": "Kio moves his arm.",
            "duration_frames": 24, "characters": ["KIO"],
            "location_id": "HALAMAN_KIO", "asset_ids": ["KIO_MASTER"],
        }],
    })


def test_pg_parallel_claims_are_atomic_and_owner_scoped():
    url = os.environ["IM19_INTEGRATION_DB_URL"]
    assert url.startswith("postgresql+psycopg://")
    vaults = [
        Vault(url, encryption_key=os.environ["IM19_INTEGRATION_FERNET_KEY"],
              audit_salt="ci-autonomy-only")
        for _ in range(4)
    ]
    engines = [AutonomousEngine(v) for v in vaults]
    owner = "ci-autonomy-" + secrets.token_hex(8)
    episode = "S91E92"
    plan = prepare_plan("prepare_arusia_episode", _manifest(episode))
    run = engines[0].create(owner, plan["plan_json"])["run_id"]
    engines[0].action(owner, run, "start")

    def race(i):
        return engines[i % 4]._claim(owner, run)

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed = list(pool.map(race, range(8)))
        successes = [result for result in claimed if result is not None]
        assert len(successes) == 1
        assert successes[0]["key"] == "inspect_manifest"
        assert engines[0]._finish(owner, successes[0], {"status": "PLAN_VALID"})["status"] == "DONE"
        assert engines[1].run_once(owner, run)["workflow_status"] == "COMPLETE"
        assert engines[2].status(owner, run)["status"] == "COMPLETE"
        with vaults[3].sessions() as session:
            steps = session.execute(select(WorkflowStep).where(
                WorkflowStep.run_id == run
            )).scalars().all()
        assert len(steps) == 2
        assert all(step.attempts == 1 for step in steps)
        assert all(step.status == "DONE" for step in steps)
    finally:
        for v in vaults:
            v.engine.dispose()
