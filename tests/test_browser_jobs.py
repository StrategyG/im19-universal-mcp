"""Browser task queue: owner isolation, exact-origin policy, safe leases and MCP."""
import json
import time
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from mcp import Client
from mcp.server import MCPServer
from sqlalchemy import update

from im19_mcp.browser_jobs import BrowserInspectionJob, BrowserJobError, BrowserJobQueue
from im19_mcp.browser_queue_worker import run_once
from im19_mcp.config import Settings
from im19_mcp.server import register_tools
from im19_mcp.storage import Vault

ORIGINS = ("https://example.com",)
KEY = "test-request-20261028"


@pytest.fixture
def q(tmp_path):
    vault = Vault("sqlite:///" + str(tmp_path / "browser.sqlite"),
                  encryption_key=Fernet.generate_key().decode(), audit_salt="synthetic-test")
    vault.initialize_schema()
    queue = BrowserJobQueue(vault)
    yield queue
    vault.engine.dispose()


def test_submit_idempotency_scope_and_sanitized_status(q):
    created = q.submit("alice", "https://example.com/news", KEY, ORIGINS)
    assert created["status"] == "CREATED" and created["browser_executed"] is False
    existing = q.submit("alice", "https://example.com/news", KEY, ORIGINS)
    assert existing["job_id"] == created["job_id"] and existing["status"] == "EXISTING"
    with pytest.raises(BrowserJobError, match="idempotency_key_conflict"):
        q.submit("alice", "https://example.com/other", KEY, ORIGINS)
    with pytest.raises(BrowserJobError, match="browser_job_not_found"):
        q.status("bob", created["job_id"])
    assert q.status("alice", created["job_id"])["status"] == "PENDING"
    assert "https://example.com/news" not in str(q.status("alice", created["job_id"]))
    assert q.submit("bob", "https://example.com/news", KEY, ORIGINS)["status"] == "CREATED"


@pytest.mark.parametrize("url", [
    "http://example.com/news", "https://127.0.0.1/private",
    "https://not-example.com/", "https://example.com:8080/",
    "https://example.com/search?token=secret", "https://example.com/#private",
    "https://example.com/path;auth", "https://user:pass@example.com/",
])
def test_submit_denies_untrusted_urls_before_database(q, url):
    with pytest.raises(BrowserJobError):
        q.submit("alice", url, KEY, ORIGINS)


def test_worker_claim_finish_and_no_credential_leak(q):
    job = q.submit("alice", "https://example.com/news", KEY, ORIGINS)
    lease = q.claim(ORIGINS)
    assert lease["job_id"] == job["job_id"]
    assert q.claim(ORIGINS) is None
    assert lease["lease_token"] not in json.dumps(q.status("alice", job["job_id"]))
    with pytest.raises(BrowserJobError, match="browser_lease_expired_or_cancelled"):
        q.finish(job["job_id"], "x" * 43, {"status": "INSPECTED"})
    assert q.finish(job["job_id"], lease["lease_token"],
                    {"status": "INSPECTED", "origin": ORIGINS[0],
                     "title": "Page title", "blocked_requests": 2})["status"] == "COMPLETED"
    status = q.status("alice", job["job_id"])
    assert status["title"] == "Page title" and status["blocked_requests"] == 2
    assert status["read_only"] is True and status["login_supported"] is False
    with pytest.raises(BrowserJobError, match="browser_lease_expired_or_cancelled"):
        q.finish(job["job_id"], lease["lease_token"], None, failed=True)


def test_cancel_while_running_invalidates_lease(q):
    job = q.submit("alice", "https://example.com", KEY, ORIGINS)
    lease = q.claim(ORIGINS)
    assert q.cancel("alice", job["job_id"])["status"] == "CANCELLED"
    with pytest.raises(BrowserJobError):
        q.finish(job["job_id"], lease["lease_token"],
                 {"status": "INSPECTED", "origin": ORIGINS[0],
                  "title": "Ignored", "blocked_requests": 0})
    assert q.status("alice", job["job_id"])["status"] == "CANCELLED"


def test_expired_lease_retries_and_budget_enforced(q):
    job = q.submit("alice", "https://example.com", KEY, ORIGINS)
    for n in range(3):
        lease = q.claim(ORIGINS)
        assert lease is not None
        with q.vault.sessions.begin() as session:
            session.execute(update(BrowserInspectionJob).where(
                BrowserInspectionJob.job_id == job["job_id"]
            ).values(lease_expires_at=time.time() - 2))
    assert q.claim(ORIGINS) is None
    result = q.status("alice", job["job_id"])
    assert result["status"] == "FAILED"
    assert result["failure_code"] == "retry_budget_exhausted"


def test_worker_rechecks_new_origin_policy(q):
    job = q.submit("alice", "https://example.com", KEY, ORIGINS)
    assert q.claim(("https://another.example",)) is None
    assert q.status("alice", job["job_id"])["failure_code"] == "worker_origin_policy_denied"


@pytest.mark.asyncio
async def test_private_worker_runner_uses_inspection_and_queue(q):
    job = q.submit("alice", "https://example.com", KEY, ORIGINS)
    async def inspector(url, *, allowed_origins):
        assert url == "https://example.com"
        assert allowed_origins == ORIGINS
        return {"status": "INSPECTED", "origin": ORIGINS[0], "title": "Synthetic", "blocked_requests": 1}
    result = await run_once(q, ORIGINS, inspector=inspector)
    assert result["status"] == "COMPLETED"
    assert q.status("alice", job["job_id"])["title"] == "Synthetic"
    assert (await run_once(q, ORIGINS, inspector=inspector))["status"] == "NO_READY_JOBS"


@pytest.mark.asyncio
async def test_mcp_contract_uses_verified_user_and_defaults_disabled(q, monkeypatch):
    monkeypatch.setenv("IM19_DEV_SUBJECT", "alice")
    enabled = replace(Settings.from_env(), enable_browser_jobs=True,
                      browser_inspection_origins=ORIGINS)
    app = MCPServer("browser queue test")
    register_tools(app, settings=enabled, vault=q.vault, oauth=None, allow_stdio_dev=True)
    async with Client(app) as client:
        found = {tool.name for tool in (await client.list_tools()).tools}
        assert {"browser_submit_inspection", "browser_inspection_status",
                "browser_cancel_inspection"} <= found
        assert "browser_click" not in found and "browser_type" not in found
        def unwrap(r):
            return r.structured_content or json.loads(r.content[0].text)
        created = unwrap(await client.call_tool("browser_submit_inspection", {
            "url": "https://example.com/test", "idempotency_key": KEY,
        }))
        assert created["status"] == "CREATED"
        job_id = created["job_id"]
        status = unwrap(await client.call_tool("browser_inspection_status", {"job_id": job_id}))
        assert status["status"] == "PENDING"
        assert unwrap(await client.call_tool("browser_cancel_inspection",
                                              {"job_id": job_id}))["status"] == "CANCELLED"
        assert unwrap(await client.call_tool("browser_submit_inspection", {
            "url": "https://example.com/?token=sensitive", "idempotency_key": KEY + "-2",
        }))["status"] == "BROWSER_JOB_DENIED"
    disabled = MCPServer("disabled browser queue")
    register_tools(disabled, settings=replace(enabled, enable_browser_jobs=False),
                   vault=q.vault, oauth=None, allow_stdio_dev=True)
    async with Client(disabled) as client:
        r = await client.call_tool("browser_submit_inspection", {
            "url": "https://example.com", "idempotency_key": KEY + "-3",
        })
        assert (r.structured_content or json.loads(r.content[0].text))["status"] == "FEATURE_DISABLED"
