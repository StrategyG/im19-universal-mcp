from __future__ import annotations

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
