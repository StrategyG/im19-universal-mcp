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
_ALLOWED_RPC_METHODS = frozenset({"GetTask", "ListTasks"})
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

    async def _get_json(self, path: str, payload: dict | None = None,
                        *, accepted_statuses: tuple[int, ...] = (200,)) -> dict:
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
                    if response.status_code not in accepted_statuses:
                        raise CloudbrowserError("UPSTREAM_UNAVAILABLE")
                    if "application/json" not in response.headers.get("content-type", "").lower():
                        raise CloudbrowserError("UPSTREAM_CONTENT_TYPE_INVALID")
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
        if method not in _ALLOWED_RPC_METHODS:
            raise CloudbrowserError("UPSTREAM_METHOD_NOT_ALLOWED")
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

    @staticmethod
    def _summarize_task(task: dict, *, expected_id: str | None = None,
                        includes_artifacts: bool = True) -> dict:
        """Produce only typed A2A evidence, never arbitrary result messages or URLs."""
        if not isinstance(task, dict):
            raise CloudbrowserError("UPSTREAM_TASK_INVALID")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise CloudbrowserError("UPSTREAM_TASK_ID_INVALID")
        if expected_id is not None and task_id != expected_id:
            raise CloudbrowserError("UPSTREAM_TASK_ID_MISMATCH")
        status = task.get("status")
        state = status.get("state") if isinstance(status, dict) else None
        if not isinstance(state, str) or state not in _A2A_STATES:
            raise CloudbrowserError("UPSTREAM_TASK_STATE_INVALID")
        metadata = task.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise CloudbrowserError("UPSTREAM_TASK_METADATA_INVALID")
        reviewed = metadata.get("workflowReviewed") is True
        updated_at = status.get("timestamp", "")
        if not isinstance(updated_at, str):
            updated_at = ""
        artifact_count = None
        if includes_artifacts:
            artifacts = task.get("artifacts", [])
            if not isinstance(artifacts, list):
                raise CloudbrowserError("UPSTREAM_TASK_ARTIFACTS_INVALID")
            artifact_count = len(artifacts)
        return {
            "id": task_id, "state": state, "is_terminal": state in _TERMINAL_STATES,
            "requires_owner_input": state == "TASK_STATE_INPUT_REQUIRED",
            "workflow_reviewed": reviewed,
            "completed_and_reviewed": state == "TASK_STATE_COMPLETED" and reviewed,
            # None means list output deliberately omitted artifact records (unknown, not zero).
            "artifact_count": artifact_count, "updated_at": updated_at[:40],
        }

    async def task_summary(self, task_id: str) -> dict:
        """Read a single task's verified status without exposing result prose or URLs."""
        task = (await self.get_task(task_id))["data"]
        return {
            "data": self._summarize_task(task, expected_id=task_id),
            "source": "cloudbrowser_a2a", "untrusted": True,
        }

    async def list_task_summaries(self, page_size: int = 10, page_token: str = "") -> dict:
        """A safe status dashboard. No raw task messages, artifact URLs or cross-page reads."""
        response = (await self.list_tasks(page_size, page_token))["data"]
        tasks = response.get("tasks")
        if not isinstance(tasks, list) or len(tasks) > page_size:
            raise CloudbrowserError("UPSTREAM_TASK_LIST_INVALID")
        size = response.get("pageSize")
        total = response.get("totalSize")
        next_token = response.get("nextPageToken", "")
        if (type(size) is not int or not 0 <= size <= 100 or
                type(total) is not int or total < 0 or
                not isinstance(next_token, str) or len(next_token) > 8 or
                (next_token and (not next_token.isascii() or not next_token.isdecimal()))):
            raise CloudbrowserError("UPSTREAM_TASK_PAGINATION_INVALID")
        summarized = [self._summarize_task(task, includes_artifacts=False) for task in tasks]
        return {
            "data": {"tasks": summarized, "page_size": size, "total_size": total,
                     "next_page_token": next_token},
            "source": "cloudbrowser_a2a", "untrusted": True,
        }

    async def readiness(self) -> dict:
        """Read Cloudbrowser's operational checks, including its expected HTTP 503 NOT READY.

        Does not assert browser / model workflow success or identity verification.
        """
        data = await self._get_json("/readyz", accepted_statuses=(200, 503))
        ready = data.get("ready")
        checks = data.get("checks")
        expected = ("database", "worker", "telegram")
        if (type(ready) is not bool or not isinstance(checks, dict) or
                any(type(checks.get(key)) is not bool for key in expected) or
                ready != all(checks[key] for key in expected)):
            raise CloudbrowserError("UPSTREAM_READINESS_INVALID")
        return {
            "data": {"ready": ready, "checks": {key: checks[key] for key in expected},
                     "operational_only": True},
            "source": "cloudbrowser_http_readyz", "untrusted": True,
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
