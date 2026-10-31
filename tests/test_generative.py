from __future__ import annotations

import httpx
import pytest

from im19_mcp.generative import GenerativeClient, GenerativeSettings


def settings():
    return GenerativeSettings(
        base_url="https://worker.example", api_key="x" * 40, enabled=True,
        allowed_profiles=("default", "image-v1"), principal_salt="s" * 40,
    )


@pytest.mark.asyncio
async def test_generation_requires_explicit_confirmation():
    client = GenerativeClient(settings())
    result = await client.submit(
        subject="user-1", stage="text_to_image", profile="default",
        prompt="A red cube", input_asset_ids=[], parameters={}, idempotency_key="request-key-000001", confirm=False,
    )
    assert result["status"] == "CONFIRMATION_REQUIRED"


@pytest.mark.asyncio
async def test_rejects_unapproved_profile_before_network():
    client = GenerativeClient(settings())
    result = await client.submit(
        subject="user-1", stage="text_to_image", profile="untrusted",
        prompt="A red cube", input_asset_ids=[], parameters={},
        idempotency_key="request-key-000001", confirm=True,
    )
    assert result == {"status": "DENY", "reason": "profile_not_allowlisted"}


@pytest.mark.asyncio
async def test_rejects_remote_asset_urls():
    client = GenerativeClient(settings())
    result = await client.submit(
        subject="user-1", stage="image_to_3d", profile="default",
        prompt="Make a 3D asset", input_asset_ids=["https://evil.example/a.png"],
        parameters={}, idempotency_key="request-key-000001", confirm=True,
    )
    assert result["status"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_rejects_unknown_parameters():
    client = GenerativeClient(settings())
    result = await client.submit(
        subject="user-1", stage="render_video", profile="default",
        prompt="Animate", input_asset_ids=["asset_123"], parameters={"shell": "bad"},
        idempotency_key="request-key-000001", confirm=True,
    )
    assert result["status"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_cancel_job_uses_authenticated_worker_contract():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        seen["principal"] = request.headers.get("X-IM19-Principal")
        return httpx.Response(200, json={"status": "cancel_requested"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = GenerativeClient(settings(), http_client=http)
        result = await client.cancel_job(subject="user-1", job_id="job_123")

    assert result == {"status": "cancel_requested", "job_id": "job_123"}
    assert seen["method"] == "POST"
    assert seen["url"] == "https://worker.example/v1/jobs/job_123/cancel"
    assert seen["authorization"] == "Bearer " + "x" * 40
    assert seen["principal"] and "user-1" not in seen["principal"]


@pytest.mark.asyncio
async def test_rejects_path_like_job_ids_before_network():
    client = GenerativeClient(settings())
    assert await client.cancel_job(subject="user-1", job_id="..") == {
        "status": "INVALID_ARGUMENT", "reason": "invalid_job_id"
    }
    assert await client.get_job(subject="user-1", job_id=".") == {
        "status": "INVALID_ARGUMENT", "reason": "invalid_job_id"
    }


@pytest.mark.asyncio
async def test_rejects_malformed_worker_progress_and_asset_ids():
    async def handler(request):
        return httpx.Response(200, json={
            "status": "running", "progress": True, "output_asset_ids": ["asset_123"]
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GenerativeClient(settings(), http_client=http)
        with pytest.raises(Exception, match="WORKER_INVALID_RESPONSE"):
            await client.get_job(subject="user-1", job_id="job_123")


@pytest.mark.asyncio
async def test_rejects_unknown_or_duplicate_worker_capabilities():
    async def handler(request):
        return httpx.Response(200, json={
            "stages": ["text_to_image", "not_a_stage", "text_to_image"]
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GenerativeClient(settings(), http_client=http)
        with pytest.raises(Exception, match="WORKER_INVALID_RESPONSE"):
            await client.capabilities(subject="user-1")


@pytest.mark.asyncio
async def test_cancel_job_rejects_unknown_worker_state():
    async def handler(request):
        return httpx.Response(200, json={"status": "done"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GenerativeClient(settings(), http_client=http)
        with pytest.raises(Exception, match="WORKER_INVALID_RESPONSE"):
            await client.cancel_job(subject="user-1", job_id="job_123")
