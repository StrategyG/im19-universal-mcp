from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from im19_mcp.upstream_runtime import (
    EnvironmentSecretResolver,
    PostgresTenantResolver,
    build_configured_upstream_router,
)
from im19_mcp.upstream_mcp import ConnectorConfigurationError


def connector_config(**overrides):
    item = {
        "connector_id": "github",
        "endpoint": "https://mcp.github.example/mcp",
        "allowed_hosts": ["mcp.github.example"],
        "secret_ref": "secret://env/GITHUB_MCP",
        "read_only_tools": ["search_repositories"],
        "tenant_allowlist": ["tenant-a"],
        "enabled": True,
    }
    item.update(overrides)
    return [item]


def test_upstream_runtime_is_disabled_without_operator_connector_config():
    assert build_configured_upstream_router(
        engine=object(),
        raw_config="",
    ) is None


def test_upstream_runtime_builds_only_from_strict_bounded_operator_config():
    router = build_configured_upstream_router(
        engine=object(),
        raw_config=json.dumps(connector_config()),
    )
    assert router is not None
    connector = router.registry.get("github")
    assert connector is not None
    assert connector.enabled is True
    assert connector.tenant_allowlist == frozenset({"tenant-a"})
    assert connector.read_only_tools == frozenset({"search_repositories"})


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        json.dumps({"connector_id": "github"}),
        json.dumps(connector_config(credential="raw-secret")),
        json.dumps(connector_config(tenant_allowlist=["tenant-a", "tenant-b"])),
        json.dumps(connector_config(endpoint="http://mcp.github.example/mcp")),
    ],
)
def test_upstream_runtime_rejects_invalid_or_untrusted_configuration(raw: str):
    with pytest.raises(ConnectorConfigurationError):
        build_configured_upstream_router(engine=object(), raw_config=raw)


def test_upstream_runtime_rejects_duplicate_json_keys():
    raw = (
        '[{"connector_id":"github","connector_id":"attacker",'
        '"endpoint":"https://mcp.github.example/mcp",'
        '"allowed_hosts":["mcp.github.example"],'
        '"secret_ref":"secret://env/GITHUB_MCP",'
        '"read_only_tools":["search_repositories"],'
        '"tenant_allowlist":["tenant-a"],"enabled":true}]'
    )
    with pytest.raises(ConnectorConfigurationError, match="duplicate"):
        build_configured_upstream_router(engine=object(), raw_config=raw)


@pytest.mark.asyncio
async def test_environment_secret_resolver_requires_secret_manager_mounted_value(monkeypatch):
    resolver = EnvironmentSecretResolver()
    monkeypatch.setenv("IM19_UPSTREAM_SECRET_GITHUB_MCP", "synthetic-short-lived-token")
    assert await resolver.resolve("secret://env/GITHUB_MCP") == "synthetic-short-lived-token"

    with pytest.raises(ConnectorConfigurationError):
        await resolver.resolve("secret://production/github/token")
    with pytest.raises(ConnectorConfigurationError):
        await resolver.resolve("secret://env/../DATABASE_URL")

    monkeypatch.delenv("IM19_UPSTREAM_SECRET_GITHUB_MCP")
    with pytest.raises(ConnectorConfigurationError, match="not_available"):
        await resolver.resolve("secret://env/GITHUB_MCP")


@pytest.mark.asyncio
async def test_postgres_tenant_resolver_requires_one_active_binding(tmp_path):
    database = tmp_path / "tenant-bindings.sqlite3"
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_subject_tenant_bindings (
                    subject TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_subject_tenant_bindings "
                "(subject, tenant_id, status) VALUES "
                "('verified-subject', 'tenant-a', 'active'), "
                "('disabled-subject', 'tenant-b', 'disabled')"
            )
        )
    resolver = PostgresTenantResolver(engine)
    assert await resolver("verified-subject") == "tenant-a"
    assert await resolver("disabled-subject") is None
    assert await resolver("unknown-subject") is None
    assert await resolver("../invalid") is None
    engine.dispose()
