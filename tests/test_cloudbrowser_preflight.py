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
