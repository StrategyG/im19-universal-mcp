"""Operator-managed IM19 upstream connector runtime wiring.

Connector definitions contain references, never credential values. Production
secrets must be injected by the deployment secret manager into the named
environment variables. Tenant resolution is authoritative and database-backed.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from im19_mcp.upstream_mcp import (
    ConnectorConfigurationError,
    ConnectorDefinition,
    ConnectorRegistry,
    UpstreamMCPRouter,
)

_SECRET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
_CONNECTOR_CONFIG_MAX_BYTES = 32 * 1024
_MAX_CONNECTORS = 32


class EnvironmentSecretResolver:
    """Resolve secret references mounted by an external deployment secret manager.

    The resolver never accepts a credential value in connector JSON. Only
    secret://env/NAME references are accepted, and the secret value itself is
    read from IM19_UPSTREAM_SECRET_NAME at runtime.
    """

    async def resolve(self, secret_ref: str) -> str:
        if not isinstance(secret_ref, str) or not secret_ref.startswith("secret://env/"):
            raise ConnectorConfigurationError("unsupported_secret_reference")
        name = secret_ref.removeprefix("secret://env/")
        if not _SECRET_NAME.fullmatch(name):
            raise ConnectorConfigurationError("secret_reference_name_invalid")
        environment_name = f"IM19_UPSTREAM_SECRET_{name.upper().replace('-', '_')}"
        value = os.environ.get(environment_name, "")
        if not isinstance(value, str) or not value or len(value) > 8192:
            raise ConnectorConfigurationError("upstream_secret_not_available")
        if any(char.isspace() for char in value):
            raise ConnectorConfigurationError("upstream_secret_format_invalid")
        return value


class PostgresTenantResolver:
    """Resolve one active tenant binding for an authenticated MCP subject.

    The mapping table is operator-managed and intentionally permits one active
    tenant per subject. Multi-tenant subject selection requires a separately
    authenticated tenant-selection contract and is not inferred from arguments.
    """

    def __init__(self, engine: Any) -> None:
        if engine is None:
            raise ValueError("tenant_resolver_database_required")
        self._engine = engine

    def _resolve_sync(self, subject: str) -> str | None:
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            return None
        with self._engine.connect() as connection:
            value = connection.execute(
                text(
                    "SELECT tenant_id FROM mcp_subject_tenant_bindings "
                    "WHERE subject = :subject AND status = 'active'"
                ),
                {"subject": subject},
            ).scalar_one_or_none()
        return value if isinstance(value, str) and value else None

    async def __call__(self, verified_subject: str) -> str | None:
        try:
            return await run_in_threadpool(self._resolve_sync, verified_subject)
        except Exception:
            # No binding or database failure always denies tool execution.
            return None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConnectorConfigurationError("duplicate_connector_config_key")
        result[key] = value
    return result


def _connector_from_config(value: object) -> ConnectorDefinition:
    if not isinstance(value, dict):
        raise ConnectorConfigurationError("connector_definition_must_be_object")
    allowed = {
        "connector_id",
        "endpoint",
        "allowed_hosts",
        "secret_ref",
        "read_only_tools",
        "tenant_allowlist",
        "enabled",
        "timeout_seconds",
        "max_result_bytes",
        "max_concurrency",
    }
    if set(value) - allowed:
        raise ConnectorConfigurationError("connector_definition_unknown_fields")
    required = {
        "connector_id",
        "endpoint",
        "allowed_hosts",
        "secret_ref",
        "read_only_tools",
        "tenant_allowlist",
    }
    if required - set(value):
        raise ConnectorConfigurationError("connector_definition_missing_fields")
    for key in ("allowed_hosts", "read_only_tools", "tenant_allowlist"):
        items = value[key]
        if (
            not isinstance(items, list)
            or not items
            or len(items) > 128
            or any(not isinstance(item, str) or not item for item in items)
            or len(set(items)) != len(items)
        ):
            raise ConnectorConfigurationError(f"connector_{key}_invalid")
    return ConnectorDefinition(
        connector_id=value["connector_id"],
        endpoint=value["endpoint"],
        allowed_hosts=frozenset(value["allowed_hosts"]),
        secret_ref=value["secret_ref"],
        read_only_tools=frozenset(value["read_only_tools"]),
        tenant_allowlist=frozenset(value["tenant_allowlist"]),
        enabled=value.get("enabled", False),
        timeout_seconds=value.get("timeout_seconds", 20.0),
        max_result_bytes=value.get("max_result_bytes", 1_000_000),
        max_concurrency=value.get("max_concurrency", 4),
    )


def build_configured_upstream_router(*, engine: Any, raw_config: str | None = None) -> UpstreamMCPRouter | None:
    """Build an immutable connector snapshot; absent config means no router."""
    raw = raw_config if raw_config is not None else os.environ.get(
        "IM19_UPSTREAM_CONNECTORS_JSON", ""
    )
    if not raw:
        return None
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _CONNECTOR_CONFIG_MAX_BYTES:
        raise ConnectorConfigurationError("connector_config_size_invalid")
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ConnectorConfigurationError("connector_config_json_invalid") from exc
    if (
        not isinstance(parsed, list)
        or len(parsed) > _MAX_CONNECTORS
    ):
        raise ConnectorConfigurationError("connector_config_must_be_bounded_array")
    connectors = [_connector_from_config(item) for item in parsed]
    registry = ConnectorRegistry(connectors)
    if not registry.enabled():
        return None
    return UpstreamMCPRouter(
        registry,
        secret_resolver=EnvironmentSecretResolver(),
        tenant_resolver=PostgresTenantResolver(engine),
    )
