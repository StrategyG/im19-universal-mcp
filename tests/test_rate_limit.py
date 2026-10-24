import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from im19_mcp.rate_limit import RateLimitMiddleware


@pytest.mark.asyncio
async def test_http_throttled_at_limit():
    app = Starlette(routes=[Route("/health", lambda _: JSONResponse({"ok": True}))])
    wrapper = RateLimitMiddleware(app, requests_per_minute=2)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapper),
                                 base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/health")).status_code == 200
        response = await client.get("/health")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"


def test_reject_invalid_limits():
    with pytest.raises(ValueError):
        RateLimitMiddleware(None, requests_per_minute=0)
