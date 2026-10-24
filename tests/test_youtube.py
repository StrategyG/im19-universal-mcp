import httpx
import pytest

from im19_mcp.youtube import ProviderError, YouTubeClient


@pytest.mark.asyncio
async def test_channel_videos_and_analytics_mock():
    def fake(request):
        if request.url.path.endswith("/channels"):
            assert request.headers["Authorization"] == "Bearer mock-token"
            return httpx.Response(200, json={"items": [{"contentDetails": {
                "relatedPlaylists": {"uploads": "uploads-1"}}}]})
        if request.url.path.endswith("/playlistItems"):
            assert request.url.params["playlistId"] == "uploads-1"
            return httpx.Response(200, json={"items": [{"snippet": {"title": "example"}}]})
        if request.url.path.endswith("/reports"):
            assert request.url.params["ids"] == "channel==MINE"
            return httpx.Response(200, json={"rows": [["2026-10-01", 1, 10, 0]]})
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
        api = YouTubeClient("mock-token", client)
        assert len((await api.videos(3))["items"]) == 1
        assert (await api.analytics("2026-10-01", "2026-10-02"))["rows"][0][1] == 1


@pytest.mark.asyncio
async def test_provider_failures_normalized():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        with pytest.raises(ProviderError, match="RATE_LIMITED"):
            await YouTubeClient("mock", client).channel()


@pytest.mark.asyncio
async def test_bad_dates_and_max_page_denied():
    api = YouTubeClient("mock")
    with pytest.raises(ValueError):
        await api.videos(51)
    with pytest.raises(ValueError):
        await api.analytics("2026-10-10", "2026-10-09")
    with pytest.raises(ValueError):
        await api.analytics("not-a-date", "2026-10-09")
    with pytest.raises(ValueError):
        await api._get("http://127.0.0.1:1234/private", {})


def test_missing_token_denied():
    with pytest.raises(ValueError):
        YouTubeClient("")
