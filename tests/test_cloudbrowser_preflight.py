"""Cloudbrowser deployment gate contracts and secret-redaction tests."""
from dataclasses import replace

import pytest

from im19_mcp.cloudbrowser_preflight import assess_cloudbrowser, main
from im19_mcp.config import Settings


def cfg():
    return Settings(
        database_url="sqlite://", vault_key="", audit_salt="",
        google_client_id="", google_client_secret="", google_redirect_uri="",
        mcp_issuer="", mcp_resource="", mcp_introspection="",
        mcp_client_id="", mcp_client_secret="", dev_subject="",
        deployment_environment="development",
        cloudbrowser_url="https://cloud.example",
        cloudbrowser_api_key="placeholder-key-" * 4,
        cloudbrowser_allowed_subjects=("owner",),
    )


@pytest.mark.asyncio
async def test_default_is_offline_and_not_production_acceptance():
    def forbidden(*args):
        pytest.fail("offline must not instantiate network client")

    report = await assess_cloudbrowser(mode="local", settings=cfg(), client_factory=forbidden)
    assert report["status"] == "NOT_PROBED"
    assert report["passed"] is False
    assert report["production_acceptance"] is False
    assert report["checks"]["configuration_valid"] is True


@pytest.mark.asyncio
async def test_live_good_readonly_still_not_production_release():
    calls = []
    class Client:
        def __init__(self, url, key):
            assert url == cfg().cloudbrowser_url
            assert key == cfg().cloudbrowser_api_key
        async def agent_card(self):
            calls.append("card")
            return {"name": "ok"}
        async def readiness(self):
            calls.append("ready")
            return {"data": {"ready": True, "checks": {
                "database": True, "worker": True, "telegram": True}}}
        async def list_task_summaries(self, page_size):
            assert page_size == 1
            calls.append("list")
            return {"data": {"tasks": [{"secret": "never_print"}]}}
    report = await assess_cloudbrowser(mode="local", live=True, settings=cfg(),
                                       client_factory=Client)
    assert report["passed"] is True
    assert report["status"] == "LIVE_READONLY_VERIFIED"
    assert report["production_acceptance"] is False
    assert "never_print" not in repr(report)
    assert "placeholder-key" not in repr(report)
    assert calls == ["card", "ready", "list"]


@pytest.mark.asyncio
async def test_not_ready_fails_after_verified_authorization():
    class Client:
        def __init__(self, *_):
            pass
        async def agent_card(self):
            return {}
        async def readiness(self):
            return {"data": {"ready": False, "checks": {
                "database": True, "worker": False, "telegram": True}}}
        async def list_task_summaries(self, page_size):
            return {}
    report = await assess_cloudbrowser(mode="local", live=True, settings=cfg(),
                                       client_factory=Client)
    assert report["passed"] is False
    assert report["checks"]["owner_a2a_authenticated"] is True
    assert report["status"] == "LIVE_NOT_OPERATIONALLY_READY"


@pytest.mark.asyncio
async def test_live_errors_are_sanitized():
    class Client:
        def __init__(self, *_):
            pass
        async def agent_card(self):
            raise RuntimeError("TOP_SECRET upstream detail")
    report = await assess_cloudbrowser(mode="local", live=True, settings=cfg(),
                                       client_factory=Client)
    assert report["status"] == "LIVE_PROBE_FAILED"
    assert "TOP_SECRET" not in repr(report)
    assert "cloud.example" not in repr(report)


@pytest.mark.asyncio
async def test_invalid_partial_configuration_refuses_network():
    invalid = replace(cfg(), cloudbrowser_api_key="")
    report = await assess_cloudbrowser(mode="local", live=True, settings=invalid,
                          client_factory=lambda *_: pytest.fail("unexpected network"))
    assert report["status"] == "INVALID_CONFIGURATION"
    assert report["passed"] is False


def test_unconfigured_cli_exits_nonzero_without_network(monkeypatch, capsys):
    for key in ("IM19_CLOUDBROWSER_URL", "IM19_CLOUDBROWSER_API_KEY",
                "IM19_CLOUDBROWSER_ALLOWED_SUBJECTS"):
        monkeypatch.delenv(key, raising=False)
    assert main(["--mode", "local"]) == 1
    assert '"production_acceptance": false' in capsys.readouterr().out



@pytest.mark.asyncio
async def test_remote_production_read_fails_closed_without_audit_or_quota(monkeypatch):
    import json
    from mcp import Client
    from mcp.server import MCPServer
    from im19_mcp.server import register_tools

    calls = {"started": 0}

    class FakeCloudbrowser:
        def __init__(self, *_):
            calls["started"] += 1

        async def agent_card(self):
            return {"name": "must never be contacted"}

    production = replace(cfg(), deployment_environment="production")
    server = MCPServer("fail-closed-production")
    # Only an authenticated subject may enter this path; the test supplies
    # a mock identity, but production configuration intentionally omits vault
    # and shared quota to prove admission is denied before network access.
    monkeypatch.setattr("im19_mcp.server.authenticated_subject",
                        lambda *, allow_stdio_dev=False: "owner")
    register_tools(server, settings=production, vault=None, oauth=None,
                   allow_stdio_dev=False, cloudbrowser_factory=FakeCloudbrowser)
    async with Client(server) as client:
        result = await client.call_tool("cloudbrowser_get_agent_card", {})
        payload = (result.structured_content if result.structured_content is not None
                   else json.loads(result.content[0].text))
        assert payload["status"] == "SERVICE_UNAVAILABLE"
        assert payload["reason"] == "governance_backend_unavailable"
    assert calls["started"] == 0


@pytest.mark.asyncio
async def test_production_stdio_never_accepts_environment_subject(monkeypatch):
    import json
    from mcp import Client
    from im19_mcp.server import create_stdio_server

    monkeypatch.setenv("IM19_DEV_SUBJECT", "owner")
    production = replace(cfg(), deployment_environment="production")
    server = create_stdio_server(settings=production)
    async with Client(server) as client:
        response = await client.call_tool("cloudbrowser_get_readiness", {})
        payload = (response.structured_content if response.structured_content is not None
                   else json.loads(response.content[0].text))
        assert payload["status"] == "AUTH_REQUIRED"
