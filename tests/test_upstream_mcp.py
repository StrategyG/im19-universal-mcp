from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.server import MCPServer

from im19_mcp.gateway import create_gateway
from im19_mcp.storage import Vault
from im19_mcp.upstream_mcp import (
    ConnectorConfigurationError,
    ConnectorDefinition,
    ConnectorRegistry,
    UpstreamMCPRouter,
    _schema_for_tool,
)


def connector(**overrides):
    values = {
        "connector_id": "github",
        "endpoint": "https://mcp.github.example/mcp",
        "allowed_hosts": frozenset({"mcp.github.example"}),
        "secret_ref": "secret://production/github/mcp-token",
        "read_only_tools": frozenset({"search_repositories"}),
        "tenant_allowlist": frozenset({"tenant-a"}),
        "enabled": True,
    }
    values.update(overrides)
    return ConnectorDefinition(**values)


class SecretResolver:
    async def resolve(self, secret_ref):
        assert secret_ref == "secret://production/github/mcp-token"
        return "synthetic-short-lived-token"


class FakeSession:
    def __init__(self, tools=None, result=None):
        self.tools = tools or []
        self.result = result or SimpleNamespace(
            isError=False,
            model_dump=lambda **_: {
                "content": [{"type": "text", "text": "synthetic result"}],
                "isError": False,
            },
        )
        self.calls = []

    async def list_tools(self, cursor=None):
        return SimpleNamespace(tools=self.tools, nextCursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


class ToolServer:
    def __init__(self, existing=()):
        self.tools = {name: object() for name in existing}
        self.handlers = {}

    async def list_tools(self):
        return [SimpleNamespace(name=name) for name in self.tools]

    def add_tool(self, fn, *, name=None, **kwargs):
        if name in self.tools:
            raise AssertionError("test server duplicate tool")
        self.tools[name] = fn
        self.handlers[name] = fn


def make_tool(name="search_repositories", annotations=None, schema=None):
    return SimpleNamespace(
        name=name,
        description="Search approved repositories",
        annotations=annotations or {},
        inputSchema=schema or {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search phrase"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )


def make_router(*, session=None, audit=None, tenant="tenant-a", quota=True, connector_def=None):
    upstream = session or FakeSession([make_tool()])
    events = []

    async def session_factory(_connector, token):
        assert token == "synthetic-short-lived-token"

    @asynccontextmanager
    async def manager(_connector, token):
        await session_factory(_connector, token)
        yield upstream

    async def audit_writer(subject, connector_id, tool_name, status, request_id):
        events.append((subject, connector_id, tool_name, status, request_id))
        return True if audit is None else await audit(
            subject, connector_id, tool_name, status, request_id,
        )

    async def quota_admission(_subject, _connector):
        return quota

    router = UpstreamMCPRouter(
        ConnectorRegistry([connector_def or connector()]),
        secret_resolver=SecretResolver(),
        tenant_resolver=lambda _subject: tenant,
        session_factory=manager,
    )
    router.bind_runtime(
        subject_resolver=lambda: "verified-subject",
        audit_writer=audit_writer,
        quota_admission=quota_admission,
    )
    return router, upstream, events


def test_connector_requires_fixed_https_host_secret_and_tenant():
    with pytest.raises(ConnectorConfigurationError):
        connector(endpoint="http://mcp.github.example/mcp")
    with pytest.raises(ConnectorConfigurationError):
        connector(endpoint="https://evil.example/mcp")
    with pytest.raises(ConnectorConfigurationError):
        connector(endpoint="https://127.0.0.1/mcp", allowed_hosts=frozenset({"127.0.0.1"}))
    with pytest.raises(ConnectorConfigurationError):
        connector(secret_ref="synthetic-token")
    with pytest.raises(ConnectorConfigurationError):
        connector(secret_ref="secret://production//github")
    with pytest.raises(ConnectorConfigurationError):
        connector(secret_ref="secret://production/../admin")
    with pytest.raises(ConnectorConfigurationError):
        connector(tenant_allowlist=frozenset({"*"}))
    with pytest.raises(ConnectorConfigurationError):
        connector(enabled="true")


def test_connector_deeply_freezes_mutable_operator_inputs():
    tools = {"search_repositories"}
    tenants = {"tenant-a"}
    item = connector(read_only_tools=tools, tenant_allowlist=tenants)
    tools.add("delete_repository")
    tenants.add("tenant-b")
    assert item.read_only_tools == frozenset({"search_repositories"})
    assert item.tenant_allowlist == frozenset({"tenant-a"})


def test_registry_is_immutable_and_revision_is_stable():
    item = connector()
    registry = ConnectorRegistry([item])
    assert registry.revision
    assert registry.get("github").revision == item.revision
    with pytest.raises(TypeError):
        registry._connectors["other"] = item
    with pytest.raises(ConnectorConfigurationError):
        ConnectorRegistry([item, item])


def test_schema_rejects_invalid_property_names_and_non_object_inputs():
    with pytest.raises(ConnectorConfigurationError):
        _schema_for_tool(make_tool(schema={"type": "array", "items": {"type": "string"}}))
    with pytest.raises(ConnectorConfigurationError):
        _schema_for_tool(make_tool(schema={
            "type": "object",
            "properties": {"bad-name": {"type": "string"}},
        }))


@pytest.mark.asyncio
async def test_router_discovers_namespaced_tool_and_executes_through_verified_identity():
    router, upstream, events = make_router()
    server = ToolServer()
    async with router.lifespan(server):
        assert "github__search_repositories" in server.handlers
        handler = server.handlers["github__search_repositories"]
        result = await handler(query="mcp", limit=5)
        assert result["status"] == "OK"
        assert result["connector"] == "github"
        assert upstream.calls == [("search_repositories", {"query": "mcp", "limit": 5})]
        assert [event[3] for event in events] == ["STARTED", "SUCCEEDED"]
        assert events[0][4] == events[1][4]


@pytest.mark.asyncio
async def test_pre_audit_failure_blocks_upstream_call():
    async def fail_start(_subject, _connector, _tool, status, _request_id):
        return status != "STARTED"

    router, upstream, _ = make_router(audit=fail_start)
    server = ToolServer()
    async with router.lifespan(server):
        result = await server.handlers["github__search_repositories"](query="blocked")
        assert result["status"] == "SERVICE_UNAVAILABLE"
        assert result["reason"] == "audit_unavailable"
        assert upstream.calls == []


@pytest.mark.asyncio
async def test_tenant_mismatch_and_quota_failure_block_upstream_call():
    router, upstream, _ = make_router(tenant="tenant-other")
    server = ToolServer()
    async with router.lifespan(server):
        result = await server.handlers["github__search_repositories"](query="blocked")
        assert result["status"] == "DENY"
        assert upstream.calls == []

    router, upstream, _ = make_router(quota=None)
    server = ToolServer()
    async with router.lifespan(server):
        result = await server.handlers["github__search_repositories"](query="blocked")
        assert result["status"] == "SERVICE_UNAVAILABLE"
        assert upstream.calls == []


@pytest.mark.asyncio
async def test_schema_validation_rejects_unknown_fields_before_upstream_call():
    router, upstream, _ = make_router()
    server = ToolServer()
    async with router.lifespan(server):
        result = await server.handlers["github__search_repositories"](
            query="mcp", injected="unexpected"
        )
        assert result["status"] == "INVALID_ARGUMENT"
        assert upstream.calls == []


@pytest.mark.asyncio
async def test_upstream_mutating_annotation_and_name_collision_fail_startup():
    mutating = make_tool(annotations={"readOnlyHint": False})
    router, _, _ = make_router(session=FakeSession([mutating]))
    with pytest.raises(ConnectorConfigurationError, match="not_read_only"):
        async with router.lifespan(ToolServer()):
            pass

    router, _, _ = make_router()
    with pytest.raises(ConnectorConfigurationError, match="collision"):
        async with router.lifespan(ToolServer(existing={"github__search_repositories"})):
            pass


@pytest.mark.asyncio
async def test_terminal_audit_failure_marks_execution_as_uncertain():
    async def fail_terminal(_subject, _connector, _tool, status, _request_id):
        return status != "SUCCEEDED"

    router, upstream, _ = make_router(audit=fail_terminal)
    server = ToolServer()
    async with router.lifespan(server):
        result = await server.handlers["github__search_repositories"](query="mcp")
        assert result["status"] == "SERVICE_UNAVAILABLE"
        assert result["execution_may_have_completed"] is True
        assert len(upstream.calls) == 1


def test_gateway_binds_optional_router_lifespan_and_runtime_governance(remote_cfg):
    vault = Vault(
        remote_cfg.database_url,
        encryption_key=remote_cfg.vault_key,
        audit_salt=remote_cfg.audit_salt,
    )
    router, _, _ = make_router()
    server = create_gateway(remote_cfg, vault=vault, upstream_router=router)
    assert server.settings.lifespan == router.lifespan
    assert router._subject_resolver is not None
    assert router._audit_writer is not None
    assert router._quota_admission is not None
    vault.engine.dispose()


@pytest.mark.asyncio
async def test_dynamic_tool_is_registered_and_callable_on_real_mcp_server():
    router, upstream, events = make_router()
    server = MCPServer("upstream-router-test")
    await router.start(server)
    try:
        async with Client(server) as client:
            discovered = await client.list_tools()
            tool = next(
                item for item in discovered.tools
                if item.name == "github__search_repositories"
            )
            assert tool.input_schema["type"] == "object"
            assert "query" in tool.input_schema["properties"]
            result = await client.call_tool(
                "github__search_repositories",
                {"query": "mcp", "limit": 3},
            )
            assert result.isError is False
        assert upstream.calls == [
            ("search_repositories", {"query": "mcp", "limit": 3})
        ]
        assert [event[3] for event in events] == ["STARTED", "SUCCEEDED"]
    finally:
        await router.close()
