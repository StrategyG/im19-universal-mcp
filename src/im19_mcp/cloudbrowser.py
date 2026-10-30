"""Read-only, bounded A2A 1.0 client for administrator-pinned Cloudbrowser.

Cloudbrowser A2A uses owner-wide credentials, so the MCP caller must also pass
a separate authenticated-subject allowlist check before invoking this client.
Never use the agent card's URL as a destination and never follow redirects.
"""
from __future__ import annotations

import json
import re

import httpx


class CloudbrowserError(Exception):
    """Sanitized protocol or upstream failure; never includes credentials."""


_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_RESPONSE_BYTES = 24_576
_A2A_STATES = frozenset({
    "TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED",
})
_TERMINAL_STATES = frozenset({
    "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED",
})


class CloudbrowserClient:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport

    async def _get_json(self, path: str, payload: dict | None = None) -> dict:
        url = self.base_url + path
        headers = {"Authorization": "Bearer " + self.api_key, "A2A-Version": "1.0",
                   "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=httpx.Timeout(12.0, connect=4.0),
                trust_env=False, follow_redirects=False,
            ) as client:
                kwargs = ({"json": payload} if payload is not None else {})
                if payload is not None:
                    kwargs["headers"] = {**headers, "Content-Type": "application/json"}
                else:
                    kwargs["headers"] = headers
                async with client.stream("POST" if payload is not None else "GET", url, **kwargs) as response:
                    if response.status_code in (401, 403):
                        raise CloudbrowserError("UPSTREAM_AUTH_DENIED")
                    if response.status_code != 200:
                        raise CloudbrowserError("UPSTREAM_UNAVAILABLE")
                    length = 0
                    chunks = []
                    async for chunk in response.aiter_bytes():
                        length += len(chunk)
                        if length > _MAX_RESPONSE_BYTES:
                            raise CloudbrowserError("UPSTREAM_RESPONSE_TOO_LARGE")
                        chunks.append(chunk)
            result = json.loads(b"".join(chunks).decode("utf-8"))
        except (httpx.HTTPError, UnicodeError, json.JSONDecodeError) as exc:
            raise CloudbrowserError("UPSTREAM_UNAVAILABLE") from exc
        if not isinstance(result, dict):
            raise CloudbrowserError("UPSTREAM_INVALID_RESPONSE")
        return result

    async def agent_card(self) -> dict:
        data = await self._get_json("/.well-known/agent-card.json")
        interfaces = data.get("supportedInterfaces", [])
        if not isinstance(interfaces, list):
            raise CloudbrowserError("UPSTREAM_INVALID_RESPONSE")
        # Reject a peer whose card does not declare the EXACT administrator-pinned RPC.
        # We never follow a card-selected endpoint.
        if not any(
            isinstance(item, dict)
            and item.get("protocolBinding") == "JSONRPC"
            and item.get("protocolVersion") == "1.0"
            and item.get("url") == self.base_url + "/a2a/rpc"
            for item in interfaces
        ):
            raise CloudbrowserError("UPSTREAM_A2A_CONTRACT_MISMATCH")
        # Do not return remote-selected endpoint URLs or auth details to the model.
        return {"name": str(data.get("name", ""))[:120],
                "version": str(data.get("version", ""))[:40],
                "protocols": [
                    {"binding": str(i.get("protocolBinding", ""))[:32],
                     "version": str(i.get("protocolVersion", ""))[:24]}
                    for i in interfaces[:5] if isinstance(i, dict)
                ],
                "source": "cloudbrowser_a2a_agent_card", "untrusted": True}

    async def _rpc(self, method: str, params: dict) -> dict:
        message = {"jsonrpc": "2.0", "id": "im19-read", "method": method, "params": params}
        data = await self._get_json("/a2a/rpc", message)
        if (data.get("jsonrpc") != "2.0" or data.get("id") != "im19-read"):
            raise CloudbrowserError("UPSTREAM_INVALID_RESPONSE")
        if "error" in data:
            raise CloudbrowserError("UPSTREAM_A2A_ERROR")
        value = data.get("result")
        if not isinstance(value, dict):
            raise CloudbrowserError("UPSTREAM_INVALID_RESPONSE")
        return {"data": value, "source": "cloudbrowser_a2a", "untrusted": True}

    async def get_task(self, task_id: str) -> dict:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ValueError("INVALID_TASK_ID")
        result = await self._rpc("GetTask", {"id": task_id, "historyLength": 0})
        if result["data"].get("id") != task_id:
            raise CloudbrowserError("UPSTREAM_TASK_ID_MISMATCH")
        return result

    async def task_summary(self, task_id: str) -> dict:
        """Return deterministic, bounded A2A state, not LLM-written status prose.

        A completed task without workflowReviewed is NOT a reviewed workflow.
        """
        result = await self.get_task(task_id)
        task = result["data"]
        status = task.get("status")
        state = status.get("state") if isinstance(status, dict) else None
        if state not in _A2A_STATES:
            raise CloudbrowserError("UPSTREAM_TASK_STATE_INVALID")
        metadata = task.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise CloudbrowserError("UPSTREAM_TASK_METADATA_INVALID")
        reviewed = metadata.get("workflowReviewed") is True
        artifacts = task.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise CloudbrowserError("UPSTREAM_TASK_ARTIFACTS_INVALID")
        updated_at = status.get("timestamp", "")
        if not isinstance(updated_at, str):
            updated_at = ""
        return {
            "data": {
                "id": task_id, "state": state, "is_terminal": state in _TERMINAL_STATES,
                "requires_owner_input": state == "TASK_STATE_INPUT_REQUIRED",
                "workflow_reviewed": reviewed,
                "completed_and_reviewed": state == "TASK_STATE_COMPLETED" and reviewed,
                "artifact_count": len(artifacts), "updated_at": updated_at[:40],
            },
            "source": "cloudbrowser_a2a", "untrusted": True,
        }

    async def list_tasks(self, page_size: int = 10, page_token: str = "") -> dict:
        if (type(page_size) is not int or not 1 <= page_size <= 20 or
                not isinstance(page_token, str) or
                (page_token and (len(page_token) > 8 or not page_token.isascii()
                                 or not page_token.isdecimal()))):
            raise ValueError("INVALID_PAGINATION")
        params = {"pageSize": page_size, "includeArtifacts": False}
        if page_token:
            params["pageToken"] = page_token
        return await self._rpc("ListTasks", params)
