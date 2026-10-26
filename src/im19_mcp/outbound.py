"""Bounded upstream reads; redirects and non-standard JSON are never accepted."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import httpx


class ResponseTooLarge(ValueError):
    pass


class InvalidResponse(ValueError):
    pass


def json_object(data: bytes) -> dict[str, Any]:
    def invalid(_: str):
        raise InvalidResponse("upstream_json_invalid")

    def finite(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise InvalidResponse("upstream_json_invalid")
        return number

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidResponse("upstream_json_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(data, parse_constant=invalid, parse_float=finite,
                           object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidResponse("upstream_json_invalid") from exc
    if not isinstance(value, dict):
        raise InvalidResponse("upstream_json_invalid")
    return value


async def request_bytes(
    method: str, url: str, *, client: httpx.AsyncClient | None = None,
    max_bytes: int = 128 * 1024, timeout: float = 12, **kwargs,
) -> bytes:
    async def read(active: httpx.AsyncClient) -> bytes:
        async with active.stream(method, url, follow_redirects=False, **kwargs) as response:
            response.raise_for_status()
            length = response.headers.get("Content-Length", "")
            if length.isdecimal() and int(length) > max_bytes:
                raise ResponseTooLarge("upstream_response_too_large")
            parts = []
            total = 0
            async for part in response.aiter_bytes():
                total += len(part)
                if total > max_bytes:
                    raise ResponseTooLarge("upstream_response_too_large")
                parts.append(part)
            return b"".join(parts)

    try:
        # HTTPX timeouts bound individual reads; also bound the entire request.
        async with asyncio.timeout(timeout):
            if client is not None:
                return await read(client)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as active:
                return await read(active)
    except TimeoutError as exc:
        raise httpx.TimeoutException("upstream_request_timed_out") from exc


async def request_json(method: str, url: str, **kwargs) -> dict[str, Any]:
    return json_object(await request_bytes(method, url, **kwargs))
