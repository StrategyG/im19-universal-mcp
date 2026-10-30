"""Cross-repository contract test: real Cloudbrowser ASGI and IM19 A2A client.

Uses a pinned, public Cloudbrowser source checkout; all traffic stays in memory.
No Telegram/Google/production secrets, network calls, model or Chromium required.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from cloudbrowser.config import Settings as CloudSettings, password_hash
from cloudbrowser.web import create_app
from im19_mcp.cloudbrowser import CloudbrowserClient, CloudbrowserError


class StubBrowser:
    context = None
    last_error = ""

    async def close(self):
        pass

    async def snapshot(self):
        return {"url": "about:blank", "elements": []}


class FakeAI:
    async def close(self):
        pass

    async def complete(self, messages, tools):
        return {"role": "assistant", "content": "contract test"}


async def verify():
    with TemporaryDirectory() as temporary:
        settings = CloudSettings(
            ai_base_url="http://127.0.0.1:9999/v1",
            ai_model="test-model",
            telegram_enabled=False,
            dashboard_password_hash=password_hash("contract-test-password"),
            a2a_base_url="http://127.0.0.1:8000",
            a2a_api_key="ONLY_FOR_TEST_contract_key_01234567890123456789",
            headless=True, chromium_sandbox=False, require_workflow=False,
        )
        app = create_app(settings, Path(temporary), start_services=False,
                         ai=FakeAI(), browser=StubBrowser())
        job = app.state.store.create_job("read-only contract test", project_id="contract")
        app.state.store._write(
            "INSERT INTO a2a_inbox VALUES (?,?,?)", (job["id"], "orchestrator", "contract"),
        )
        transport = httpx.ASGITransport(app=app)
        connector = CloudbrowserClient(settings.a2a_base_url, settings.a2a_api_key,
                                       transport=transport)
        try:
            card = await connector.agent_card()
            assert card["name"] == "Cloudbrowser Orchestrator", card
            assert {"binding": "JSONRPC", "version": "1.0"} in card["protocols"], card
            task = await connector.get_task(job["id"])
            assert task["data"]["id"] == job["id"], task
            tasks = await connector.list_tasks()
            assert tasks["data"]["tasks"][0]["id"] == job["id"], tasks

            # A2A credentials are owner-wide; never silently accept an invalid key.
            wrong = CloudbrowserClient(settings.a2a_base_url, "invalid-key", transport=transport)
            try:
                await wrong.get_task(job["id"])
            except CloudbrowserError as error:
                assert str(error) == "UPSTREAM_AUTH_DENIED", str(error)
            else:
                raise AssertionError("A2A rejected-key test must fail closed")
            print("PASS Cloudbrowser A2A 1.0: discovery, GetTask, ListTasks, deny wrong bearer")
        finally:
            await app.state.agent.close()
            app.state.store.close()


if __name__ == "__main__":
    asyncio.run(verify())
