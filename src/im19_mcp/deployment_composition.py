"""Operator-controlled, fail-closed upstream MCP deployment composition.

This first deployment adapter deliberately supports mounted file secrets and a
static verified-subject-to-tenant map. It is suitable for a bounded staging
pilot. Production identity revocation and dynamic tenant-directory integration
must be supplied by the deployment before enabling broad production traffic.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from im19_mcp.upstream_mcp import (
    ConnectorDefinition,
    ConnectorRegistry,
    SecretResolver,
    UpstreamMCPRouter,
)

_MAX_JSON_BYTES = 64 * 1024
_MAX_CONNECTORS = 50
_MAX_TENANT_SUBJECTS = 10_000
_MAX_SECRET_BYTES = 8192
_SUBJECT = re.compile(r"^[^\s\x00-\x1f\x7f]{1,255}$")
_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONNECTOR_KEYS = frozenset(
    {
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
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _parse_json(raw: str, *, name: str) -> Any:
    if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError(f"{name}_missing_or_too_large")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        # object_pairs_hook raises ValueError for duplicate keys; normalize it
        # at the configuration boundary so callers receive a stable error code.
        raise ValueError(f"{name}_invalid_json") from exc


class FileSecretResolver(SecretResolver):
    """Resolve secret:// references beneath a mounted secret root without symlinks."""

    def __init__(self, root: str | Path) -> None:
        if not isinstance(root, (str, Path)) or not str(root).strip():
            raise ValueError("upstream_secret_root_invalid")
        self._root = Path(root)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self._root, flags)
        except OSError as exc:
            raise ValueError("upstream_secret_root_unavailable") from exc
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise ValueError("upstream_secret_root_not_directory")
        finally:
            os.close(fd)

    async def resolve(self, secret_ref: str) -> str:
        if not isinstance(secret_ref, str) or not secret_ref.startswith("secret://"):
            raise ValueError("upstream_secret_reference_invalid")
        relative = secret_ref.removeprefix("secret://")
        parts = relative.split("/")
        if (
            len(parts) < 2
            or any(
                not part
                or part in {".", ".."}
                or any(ord(char) < 33 or ord(char) == 127 for char in part)
                for part in parts
            )
        ):
            raise ValueError("upstream_secret_reference_invalid")

        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptors: list[int] = []
        try:
            descriptors.append(os.open(self._root, directory_flags))
            for part in parts[:-1]:
                descriptors.append(
                    os.open(part, directory_flags, dir_fd=descriptors[-1])
                )
            secret_fd = os.open(parts[-1], file_flags, dir_fd=descriptors[-1])
            descriptors.append(secret_fd)
            metadata = os.fstat(secret_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("upstream_secret_not_regular_file")
            if metadata.st_mode & 0o022:
                raise ValueError("upstream_secret_permissions_too_open")
            if metadata.st_size < 1 or metadata.st_size > _MAX_SECRET_BYTES:
                raise ValueError("upstream_secret_size_invalid")
            raw = os.read(secret_fd, _MAX_SECRET_BYTES + 1)
            if not 1 <= len(raw) <= _MAX_SECRET_BYTES:
                raise ValueError("upstream_secret_size_invalid")
            try:
                value = raw.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise ValueError("upstream_secret_encoding_invalid") from exc
            if not value or len(value) > _MAX_SECRET_BYTES or any(ch.isspace() for ch in value):
                raise ValueError("upstream_secret_value_invalid")
            return value
        except OSError as exc:
            # Do not expose filesystem paths, mount layout or secret references.
            raise ValueError("upstream_secret_unavailable") from exc
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass


class StaticTenantResolver:
    """Exact, immutable mapping for a single configured IM19 issuer."""

    def __init__(self, mapping: Mapping[str, str]) -> None:
        if not isinstance(mapping, Mapping) or not 1 <= len(mapping) <= _MAX_TENANT_SUBJECTS:
            raise ValueError("upstream_tenant_map_invalid")
        normalized: dict[str, str] = {}
        for subject, tenant_id in mapping.items():
            if (
                not isinstance(subject, str)
                or subject in {"*", "**"}
                or not _SUBJECT.fullmatch(subject)
                or not isinstance(tenant_id, str)
                or not _TENANT.fullmatch(tenant_id)
                or subject in normalized
            ):
                raise ValueError("upstream_tenant_map_entry_invalid")
            normalized[subject] = tenant_id
        self._mapping = MappingProxyType(normalized)

    def __call__(self, verified_subject: str) -> str | None:
        if not isinstance(verified_subject, str) or not _SUBJECT.fullmatch(verified_subject):
            return None
        return self._mapping.get(verified_subject)


def build_upstream_router(settings: Any) -> UpstreamMCPRouter | None:
    """Build the optional router from operator config; partial config fails closed.

    With no connector or tenant-map configuration the router stays disabled.
    Supplying only one configuration document is an error, never a fallback.
    """
    connectors_raw = getattr(settings, "upstream_connectors_json", "")
    tenants_raw = getattr(settings, "upstream_tenant_map_json", "")
    configured = bool(connectors_raw or tenants_raw)
    if not configured:
        return None
    if not connectors_raw or not tenants_raw:
        raise ValueError("upstream_runtime_configuration_incomplete")

    raw_connectors = _parse_json(connectors_raw, name="upstream_connectors")
    raw_tenants = _parse_json(tenants_raw, name="upstream_tenant_map")
    if not isinstance(raw_connectors, list) or not 1 <= len(raw_connectors) <= _MAX_CONNECTORS:
        raise ValueError("upstream_connector_list_invalid")
    if not isinstance(raw_tenants, dict):
        raise ValueError("upstream_tenant_map_must_be_object")

    tenant_resolver = StaticTenantResolver(raw_tenants)
    definitions: list[ConnectorDefinition] = []
    for item in raw_connectors:
        if not isinstance(item, dict) or set(item) - _CONNECTOR_KEYS:
            raise ValueError("upstream_connector_definition_invalid")
        required = {
            "connector_id",
            "endpoint",
            "allowed_hosts",
            "secret_ref",
            "read_only_tools",
            "tenant_allowlist",
            "enabled",
        }
        if not required.issubset(item):
            raise ValueError("upstream_connector_definition_incomplete")
        for key in ("allowed_hosts", "read_only_tools", "tenant_allowlist"):
            if (
                not isinstance(item[key], list)
                or not item[key]
                or any(not isinstance(value, str) or not value for value in item[key])
                or len(set(item[key])) != len(item[key])
            ):
                raise ValueError(f"upstream_{key}_invalid")
        if not isinstance(item["enabled"], bool):
            raise ValueError("upstream_connector_enabled_invalid")

        tenant_allowlist = frozenset(item["tenant_allowlist"])
        known_tenants = frozenset(raw_tenants.values())
        if not tenant_allowlist.issubset(known_tenants):
            raise ValueError("upstream_connector_tenant_not_mapped")

        options = {
            key: item[key]
            for key in ("timeout_seconds", "max_result_bytes", "max_concurrency")
            if key in item
        }
        definitions.append(
            ConnectorDefinition(
                connector_id=item["connector_id"],
                endpoint=item["endpoint"],
                allowed_hosts=frozenset(item["allowed_hosts"]),
                secret_ref=item["secret_ref"],
                read_only_tools=frozenset(item["read_only_tools"]),
                tenant_allowlist=tenant_allowlist,
                enabled=item["enabled"],
                **options,
            )
        )

    resolver = FileSecretResolver(
        getattr(settings, "upstream_secret_root", "/run/secrets/im19/upstream")
    )
    return UpstreamMCPRouter(
        ConnectorRegistry(definitions),
        secret_resolver=resolver,
        tenant_resolver=tenant_resolver,
    )
