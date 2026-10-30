"""Cloudbrowser A2A client and MCP authorization contract; no external calls."""
from dataclasses import replace
import httpx
import pytest

from im19_mcp.cloudbrowser import CloudbrowserClient, CloudbrowserError
from im19_mcp.config import Settings
from im19_mcp.registry import SERVICES


def settings():
    return Settings(
        database_url="sqlite://", vault_key="", audit_salt="",
        google_client_id="", google_client_secret="", google_redirect_uri="",
        mcp_issuer="", mcp_resource="", mcp_introspection="",
        mcp_client_id="", mcp_client_secret="", dev_subject="",
    )


def test_cloudbrowser_not_claimed_without_explicit_configuration():
    assert SERVICES["cloudbrowser"].implemented
    assert not settings().cloudbrowser_enabled()


def test_config_requires_pinned_endpoint_secret_and_subject_allowlist():
    cfg = replace(settings(), cloudbrowser_url="https://agent.example",
                  cloudbrowser_api_key="a" * 40,
                  cloudbrowser_allowed_subjects=("trusted-user",))
    cfg.validate_cloudbrowser()
    for url in ("http://agent.example", "https://agent.example/redirect",
                "https://u:p@agent.example", "https://agent.example?x=1"):
        with pytest.raises(ValueError):
            replace(cfg, cloudbrowser_url=url).validate_cloudbrowser()
    with pytest.raises(ValueError):
        replace(cfg, cloudbrowser_allowed_subjects=()).validate_cloudbrowser()
    with pytest.raises(ValueError):
        replace(cfg, cloudbrowser_api_key="short").validate_cloudbrowser()
    with pytest.raises(ValueError):
        replace(cfg, cloudbrowser_api_key="").validate_cloudbrowser()
    replace(cfg, cloudbrowser_url="http://127.0.0.1:8000").validate_cloudbrowser()


@pytest.mark.asyncio
async def test_cloudbrowser_a2a_methods_are_fixed_and_authenticated():
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.path.endswith("agent-card.json"):
            return httpx.Response(200, json={"name": "Cloudbrowser",
                "version": "0.4", "supportedInterfaces": [
                    {"protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                     "url": "https://cloud.example/a2a/rpc"}]})
        body = __import__("json").loads(request.content)
        if body["method"] == "GetTask":
            assert body["params"]["id"] == "task-1"
            return httpx.Response(200, json={"jsonrpc": "2.0",
                   "id": "im19-read", "result": {"id": "task-1"}})
        assert body["method"] == "ListTasks"
        assert body["params"] == {"pageSize": 10, "includeArtifacts": False}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "im19-read",
                                             "result": {"tasks": []}})

    client = CloudbrowserClient("https://cloud.example", "test-private-key",
                                transport=httpx.MockTransport(respond))
    card = await client.agent_card()
    assert card["protocols"][0]["version"] == "1.0"
    assert "https://cloud.example/a2a/rpc" not in repr(card)
    assert (await client.get_task("task-1"))["data"]["id"] == "task-1"
    assert (await client.list_tasks())["data"]["tasks"] == []
    assert len(seen) == 3
    assert all(r.headers["authorization"] == "Bearer test-private-key" for r in seen)
    assert all(r.url.host == "cloud.example" for r in seen)
    assert all(r.headers["a2a-version"] == "1.0" for r in seen)


@pytest.mark.asyncio
async def test_invalid_inputs_are_rejected_before_network():
    client = CloudbrowserClient("https://cloud.example", "key",
                                transport=httpx.MockTransport(
                                    lambda _: pytest.fail("unexpected network call")))
    for task_id in ("../../admin", "a/b", "", "!" * 130):
        with pytest.raises(ValueError):
            await client.get_task(task_id)
    for size, cursor in ((21, ""), (0, ""), (True, ""), (5, "bad?token")):
        with pytest.raises(ValueError):
            await client.list_tasks(size, cursor)


@pytest.mark.asyncio
async def test_upstream_errors_and_redirects_are_fail_closed():
    for response in (httpx.Response(302, headers={"location": "https://evil.example"}),
                     httpx.Response(401),
                     httpx.Response(200, content=b"not json"),
                     httpx.Response(200, content=b"x" * 25000),
                     httpx.Response(200, json={"jsonrpc": "2.0", "id": "im19-read",
                                                 "error": {"code": -32001}})):
        client = CloudbrowserClient("https://cloud.example", "key",
                                    transport=httpx.MockTransport(lambda _: response))
        with pytest.raises(CloudbrowserError):
            await client.list_tasks()


@pytest.mark.asyncio
async def test_mcp_discovery_and_subject_isolation(monkeypatch):
    from mcp import Client
    from mcp.server import MCPServer
    from im19_mcp.server import register_tools

    class FakeCloudbrowser:
        async def agent_card(self):
            return {"name": "demo", "untrusted": True}

        async def get_task(self, task_id):
            return {"data": {"id": task_id}, "untrusted": True}

        async def task_summary(self, task_id):
            return {"data": {"id": task_id, "state": "TASK_STATE_WORKING"},
                    "untrusted": True}

        async def list_task_summaries(self, page_size, page_token):
            return {"data": {"tasks": []}, "untrusted": True}

        async def readiness(self):
            return {"data": {"ready": False}, "untrusted": True}

        async def list_tasks(self, page_size, page_token):
            return {"data": {"tasks": []}, "untrusted": True}

    cfg = replace(settings(), cloudbrowser_url="https://cloud.example",
                  cloudbrowser_api_key="k" * 40,
                  cloudbrowser_allowed_subjects=("trusted-subject",))
    server = MCPServer("IM19 Cloudbrowser test")
    register_tools(server, settings=cfg, vault=None, oauth=None, allow_stdio_dev=True,
                   cloudbrowser_factory=lambda url, secret: FakeCloudbrowser())
    monkeypatch.setenv("IM19_DEV_SUBJECT", "trusted-subject")
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {"cloudbrowser_get_agent_card", "cloudbrowser_get_task",
                "cloudbrowser_get_task_summary", "cloudbrowser_list_tasks",
                "cloudbrowser_list_task_summaries", "cloudbrowser_get_readiness"}.issubset(names)
        ok = await client.call_tool("cloudbrowser_get_task", {"task_id": "job123"})
        assert not ok.is_error, str(ok.content)
        from json import loads
        payload = ok.structured_content if ok.structured_content is not None else loads(ok.content[0].text)
        assert payload["status"] == "OK"
        assert payload["data"]["id"] == "job123"
        monkeypatch.setenv("IM19_DEV_SUBJECT", "other-subject")
        denied = await client.call_tool("cloudbrowser_list_tasks", {})
        payload = denied.structured_content if denied.structured_content is not None else loads(denied.content[0].text)
        assert payload["status"] == "DENY"
        monkeypatch.delenv("IM19_DEV_SUBJECT")
        no_identity = await client.call_tool("cloudbrowser_get_agent_card", {})
        payload = (no_identity.structured_content if no_identity.structured_content is not None
                   else loads(no_identity.content[0].text))
        assert payload["status"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_precise_workflow_state_summary():
    import json

    state = {"id": "task-1", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                     "timestamp": "2026-10-30T00:00:00Z"},
             "metadata": {"workflowReviewed": False},
             "artifacts": [{"artifactId": "a1", "parts": [{"url": "https://private.test"}]}]}

    def respond(request):
        args = json.loads(request.content)
        assert args["method"] == "GetTask"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "im19-read", "result": state})

    client = CloudbrowserClient("https://cloud.example", "test-key",
                                transport=httpx.MockTransport(respond))
    summary = (await client.task_summary("task-1"))["data"]
    assert summary["requires_owner_input"] is True
    assert summary["is_terminal"] is False
    assert summary["completed_and_reviewed"] is False
    assert summary["artifact_count"] == 1
    assert "private.test" not in str(summary)

    state["status"]["state"] = "TASK_STATE_COMPLETED"
    summary = (await client.task_summary("task-1"))["data"]
    assert summary["is_terminal"] is True and summary["completed_and_reviewed"] is False
    state["metadata"]["workflowReviewed"] = True
    summary = (await client.task_summary("task-1"))["data"]
    assert summary["completed_and_reviewed"] is True
    state["status"]["state"] = "UNKNOWN"
    with pytest.raises(CloudbrowserError, match="UPSTREAM_TASK_STATE_INVALID"):
        await client.task_summary("task-1")
    state["id"] = "another-task"
    with pytest.raises(CloudbrowserError, match="UPSTREAM_TASK_ID_MISMATCH"):
        await client.task_summary("task-1")


@pytest.mark.asyncio
async def test_agent_card_must_pin_declared_rpc_origin():
    for provided_url in ("https://evil.example/a2a/rpc",
                         "https://cloud.example/another-route", ""):
        client = CloudbrowserClient(
            "https://cloud.example", "key",
            transport=httpx.MockTransport(lambda _: httpx.Response(
                200, json={"name": "Cloudbrowser", "supportedInterfaces": [{
                    "protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                    "url": provided_url}]})),
        )
        with pytest.raises(CloudbrowserError, match="UPSTREAM_A2A_CONTRACT_MISMATCH"):
            await client.agent_card()


@pytest.mark.asyncio
async def test_readiness_reports_unhealthy_cloudbrowser_without_success_claim():
    client = CloudbrowserClient("https://cloud.example", "key",
        transport=httpx.MockTransport(lambda req: httpx.Response(
            503, json={"ready": False, "checks": {"database": True, "worker": False,
                                                "telegram": True},
                       "upstream_secret": "NEVER_REFLECT"})))
    result = await client.readiness()
    assert result["data"]["ready"] is False
    assert result["data"]["checks"]["worker"] is False
    assert "upstream_secret" not in repr(result)


@pytest.mark.asyncio
async def test_readiness_rejects_forged_or_malformed_boolean_fields():
    for payload in ({"ready": True, "checks": {"database": True, "worker": False, "telegram": True}},
                    {"ready": "true", "checks": {}},
                    {"ready": False, "checks": {"database": True, "worker": None, "telegram": True}}):
        client = CloudbrowserClient("https://cloud.example", "key",
            transport=httpx.MockTransport(lambda req: httpx.Response(503, json=payload)))
        with pytest.raises(CloudbrowserError, match="UPSTREAM_READINESS_INVALID"):
            await client.readiness()


@pytest.mark.asyncio
async def test_list_summary_strips_messages_and_reports_omitted_artifacts_as_unknown():
    import json

    response = {"jsonrpc": "2.0", "id": "im19-read",
                "result": {"tasks": [
                    {"id": "job-1", "status": {"state": "TASK_STATE_COMPLETED",
                                             "message": {"parts": [{"text": "PRIVATE-NOTE"}]}},
                     "metadata": {"workflowReviewed": True},
                     "contextId": "PRIVATE-CONTEXT"}],
                           "pageSize": 10, "totalSize": 1, "nextPageToken": ""}}
    def respond(request):
        body = json.loads(request.content)
        assert body["method"] == "ListTasks"
        assert body["params"]["includeArtifacts"] is False
        return httpx.Response(200, json=response)

    client = CloudbrowserClient("https://cloud.example", "key",
        transport=httpx.MockTransport(respond))
    data = (await client.list_task_summaries())["data"]
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["completed_and_reviewed"] is True
    assert data["tasks"][0]["artifact_count"] is None
    assert data["total_size"] == 1
    assert "PRIVATE-NOTE" not in repr(data)
    assert "PRIVATE-CONTEXT" not in repr(data)
    response["result"]["tasks"][0]["id"] = "bad/id"
    with pytest.raises(CloudbrowserError, match="UPSTREAM_TASK_ID_INVALID"):
        await client.list_task_summaries()
    response["result"]["tasks"][0]["id"] = "job-1"
    response["result"]["nextPageToken"] = "not-a-page"
    with pytest.raises(CloudbrowserError, match="UPSTREAM_TASK_PAGINATION_INVALID"):
        await client.list_task_summaries()


@pytest.mark.asyncio
async def test_rpc_method_allowlist_denies_mutating_methods_without_network():
    client = CloudbrowserClient("https://cloud.example", "key",
        transport=httpx.MockTransport(lambda _: pytest.fail("must not call upstream")))
    with pytest.raises(CloudbrowserError, match="UPSTREAM_METHOD_NOT_ALLOWED"):
        await client._rpc("SendMessage", {"message": {"parts": [{"text": "unsafe"}]}})


@pytest.mark.asyncio
async def test_cloudbrowser_mcp_refuses_unlogged_read(monkeypatch):
    from mcp import Client
    from mcp.server import MCPServer
    from im19_mcp.server import register_tools

    counter = {"requests": 0}

    class FailingVault:
        def audit(self, *args, **kwargs):
            raise OSError("audit backend unavailable")

    class Factory:
        def __init__(self, *args):
            counter["requests"] += 1

        async def agent_card(self):
            return {"name": "should not run"}

    cfg = replace(settings(), cloudbrowser_url="https://cloud.example",
                  cloudbrowser_api_key="z" * 40,
                  cloudbrowser_allowed_subjects=("user-1",))
    server = MCPServer("audit-required")
    register_tools(server, settings=cfg, vault=FailingVault(), oauth=None,
                   allow_stdio_dev=True, cloudbrowser_factory=Factory)
    monkeypatch.setenv("IM19_DEV_SUBJECT", "user-1")
    async with Client(server) as mcp:
        response = await mcp.call_tool("cloudbrowser_get_agent_card", {})
        import json
        data = (response.structured_content if response.structured_content is not None
                else json.loads(response.content[0].text))
        assert data == {"status": "SERVICE_UNAVAILABLE", "reason": "audit_unavailable"}
    assert counter["requests"] == 0
