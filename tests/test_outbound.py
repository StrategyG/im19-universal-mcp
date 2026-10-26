"""Upstream failures are bounded before JSON parsing or credential storage."""
import asyncio
import gzip

import httpx
import pytest

from im19_mcp.outbound import InvalidResponse, ResponseTooLarge, request_bytes, request_json
from im19_mcp.youtube import ProviderError, YouTubeClient


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b'[]', b'{"exp":NaN}', b'{"exp":Infinity}',
                                     b'{"exp":1e999}', b'{"active":false,"active":true}', b'{'])
async def test_nonstandard_or_ambiguous_json_is_rejected(raw):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=raw),
    )) as client:
        with pytest.raises(InvalidResponse):
            await request_json("GET", "https://provider.test", client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize("compressed", [False, True])
async def test_response_limit_applies_to_decoded_bytes(compressed):
    raw = b"x" * 4096
    headers = {"Content-Encoding": "gzip"} if compressed else {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=gzip.compress(raw) if compressed else raw,
                                 headers=headers),
    )) as client:
        with pytest.raises(ResponseTooLarge):
            await request_bytes("GET", "https://provider.test", client=client, max_bytes=256)


@pytest.mark.asyncio
async def test_redirects_never_forward_upstream_credentials():
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.test"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply), follow_redirects=True) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_json("GET", "https://provider.test", client=client,
                               headers={"Authorization": "Bearer synthetic-secret"})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_total_deadline_stops_a_slow_trickle():
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(0.01)
                yield b"x"

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=SlowStream()),
    )) as client:
        with pytest.raises(httpx.TimeoutException):
            await request_bytes("GET", "https://provider.test", client=client, timeout=0.03)


@pytest.mark.asyncio
async def test_youtube_response_cannot_allocate_unbounded_json():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"x" * (513 * 1024)),
    )) as client:
        with pytest.raises(ProviderError, match="RESPONSE_SIZE_LIMIT_EXCEEDED"):
            await YouTubeClient("synthetic-secret", client).channel()
