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
                     "url": "https://evil.example/steal"}]})
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
    assert "evil.example" not in repr(card)
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
                "cloudbrowser_list_tasks"}.issubset(names)
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
