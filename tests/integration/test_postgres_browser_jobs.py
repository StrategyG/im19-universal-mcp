"""Real PostgreSQL 16 race regression for dedicated BrowserJobQueue replicas."""
import os
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from im19_mcp.browser_jobs import BrowserInspectionJob, BrowserJobQueue
from im19_mcp.storage import Vault

pytestmark = pytest.mark.skipif(
    not os.getenv("IM19_INTEGRATION_DB_URL"),
    reason="Requires disposable PostgreSQL integration database",
)

ORIGIN = ("https://example.com",)


def test_postgres_exactly_one_concurrent_browser_lease():
    url = os.environ["IM19_INTEGRATION_DB_URL"]
    assert url.startswith("postgresql+psycopg://")
    vaults = [
        Vault(url, encryption_key=Fernet.generate_key().decode(),
              audit_salt="ci-browser-race")
        for _ in range(4)
    ]
    queues = [BrowserJobQueue(v) for v in vaults]
    owner = "pg-browser-" + secrets.token_hex(12)
    request_key = "pg-browser-key-" + secrets.token_hex(8)
    record = queues[0].submit(owner, "https://example.com/info", request_key, ORIGIN)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            attempts = list(pool.map(lambda i: queues[i % 4].claim(ORIGIN), range(8)))
        winners = [claim for claim in attempts if claim is not None]
        assert len(winners) == 1
        assert winners[0]["job_id"] == record["job_id"]
        assert queues[0].status(owner, record["job_id"])["attempts"] == 1
        with vaults[0].sessions() as session:
            row = session.execute(select(BrowserInspectionJob).where(
                BrowserInspectionJob.job_id == record["job_id"],
            )).scalar_one()
            assert row.state == "RUNNING" and row.attempts == 1
        queues[1].finish(record["job_id"], winners[0]["lease_token"],
                         {"status": "INSPECTED", "origin": ORIGIN[0],
                          "title": "Synthetic verified", "blocked_requests": 0})
        assert queues[2].status(owner, record["job_id"])["status"] == "COMPLETED"
    finally:
        for v in vaults:
            v.engine.dispose()
