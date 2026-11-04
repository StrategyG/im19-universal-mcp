from __future__ import annotations

import json
from pathlib import Path

import pytest

from im19_mcp.config import Settings
from im19_mcp.deployment_composition import (
    FileSecretResolver,
    StaticTenantResolver,
    build_upstream_router,
)


def connector_config(**overrides):
    values = {
        "connector_id": "github",
        "endpoint": "https://mcp.github.example/mcp",
        "allowed_hosts": ["mcp.github.example"],
        "secret_ref": "secret://production/github/mcp-token",
        "read_only_tools": ["search_repositories"],
        "tenant_allowlist": ["tenant-a"],
        "enabled": True,
    }
    values.update(overrides)
    return values


def configured_settings(monkeypatch, root: Path, connectors, tenants):
    monkeypatch.setenv("IM19_UPSTREAM_CONNECTORS_JSON", json.dumps(connectors))
    monkeypatch.setenv("IM19_UPSTREAM_TENANT_MAP_JSON", json.dumps(tenants))
    monkeypatch.setenv("IM19_UPSTREAM_SECRET_ROOT", str(root))
    return Settings.from_env()


def create_secret(root: Path, *, name: str = "mcp-token", value: str = "synthetic-token") -> Path:
    path = root / "production" / "github" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_upstream_router_is_disabled_without_operator_configuration(monkeypatch) -> None:
    for name in (
        "IM19_UPSTREAM_CONNECTORS_JSON",
        "IM19_UPSTREAM_TENANT_MAP_JSON",
        "IM19_UPSTREAM_SECRET_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert build_upstream_router(settings) is None


def test_complete_operator_configuration_builds_fail_closed_router(
    monkeypatch, tmp_path: Path
) -> None:
    create_secret(tmp_path)
    settings = configured_settings(
        monkeypatch,
        tmp_path,
        [connector_config()],
        {"verified-user-123": "tenant-a"},
    )

    router = build_upstream_router(settings)

    assert router is not None
    assert router.registry.enabled()[0].connector_id == "github"
    assert router._tenant_for_subject("verified-user-123") == "tenant-a"
    assert router._tenant_for_subject("unknown-user") is None


def test_partial_operator_configuration_fails_closed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IM19_UPSTREAM_CONNECTORS_JSON", json.dumps([connector_config()]))
    monkeypatch.delenv("IM19_UPSTREAM_TENANT_MAP_JSON", raising=False)
    monkeypatch.setenv("IM19_UPSTREAM_SECRET_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="upstream_runtime_configuration_incomplete"):
        build_upstream_router(Settings.from_env())


def test_duplicate_json_keys_are_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "IM19_UPSTREAM_CONNECTORS_JSON",
        '[{"connector_id":"github","connector_id":"other"}]',
    )
    monkeypatch.setenv("IM19_UPSTREAM_TENANT_MAP_JSON", '{"verified-user-123":"tenant-a"}')
    monkeypatch.setenv("IM19_UPSTREAM_SECRET_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="upstream_connectors_invalid_json"):
        build_upstream_router(Settings.from_env())


def test_connector_cannot_reference_unmapped_tenant(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "IM19_UPSTREAM_CONNECTORS_JSON",
        json.dumps([connector_config(tenant_allowlist=["tenant-b"])]),
    )
    monkeypatch.setenv("IM19_UPSTREAM_TENANT_MAP_JSON", '{"verified-user-123":"tenant-a"}')
    monkeypatch.setenv("IM19_UPSTREAM_SECRET_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="upstream_connector_tenant_not_mapped"):
        build_upstream_router(Settings.from_env())


def test_unknown_connector_configuration_fields_are_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "IM19_UPSTREAM_CONNECTORS_JSON",
        json.dumps([connector_config(arbitrary_headers={"Authorization": "model-controlled"})]),
    )
    monkeypatch.setenv("IM19_UPSTREAM_TENANT_MAP_JSON", '{"verified-user-123":"tenant-a"}')
    monkeypatch.setenv("IM19_UPSTREAM_SECRET_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="upstream_connector_definition_invalid"):
        build_upstream_router(Settings.from_env())


@pytest.mark.asyncio
async def test_file_secret_resolver_reads_only_beneath_root(tmp_path: Path) -> None:
    create_secret(tmp_path)
    resolver = FileSecretResolver(tmp_path)

    assert await resolver.resolve("secret://production/github/mcp-token") == "synthetic-token"
    with pytest.raises(ValueError, match="upstream_secret_reference_invalid"):
        await resolver.resolve("secret://production/../outside")


@pytest.mark.asyncio
async def test_file_secret_resolver_rejects_symlinks(tmp_path: Path) -> None:
    create_secret(tmp_path)
    secret_dir = tmp_path / "production" / "github"
    external = tmp_path / "outside-secret"
    external.write_text("not-allowed", encoding="utf-8")
    external.chmod(0o600)
    secret_link = secret_dir / "linked-token"
    secret_link.symlink_to(external)
    resolver = FileSecretResolver(tmp_path)

    with pytest.raises(ValueError, match="upstream_secret_unavailable"):
        await resolver.resolve("secret://production/github/linked-token")


@pytest.mark.asyncio
async def test_file_secret_resolver_rejects_group_or_world_writable_files(tmp_path: Path) -> None:
    path = create_secret(tmp_path)
    path.chmod(0o622)
    resolver = FileSecretResolver(tmp_path)

    with pytest.raises(ValueError, match="upstream_secret_permissions_too_open"):
        await resolver.resolve("secret://production/github/mcp-token")


def test_static_tenant_resolver_is_exact_and_immutable() -> None:
    resolver = StaticTenantResolver({"verified-user-123": "tenant-a"})
    assert resolver("verified-user-123") == "tenant-a"
    assert resolver("verified-user-123 ") is None
    assert resolver("other-user") is None
    with pytest.raises(TypeError):
        resolver._mapping["other-user"] = "tenant-a"


def test_static_tenant_resolver_rejects_wildcards_and_blank_mapping() -> None:
    with pytest.raises(ValueError, match="upstream_tenant_map_entry_invalid"):
        StaticTenantResolver({"*": "tenant-a"})
    with pytest.raises(ValueError, match="upstream_tenant_map_invalid"):
        StaticTenantResolver({})
