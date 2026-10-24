"""Read-only YouTube Data v3 and Analytics v2 clients using fixed official hosts."""
from __future__ import annotations

from datetime import date

import httpx

DATA_URL = "https://www.googleapis.com/youtube/v3"
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"


class ProviderError(Exception):
    """Normalized provider failures with no token, URL query, or response body."""


class YouTubeClient:
    def __init__(self, access_token: str, client: httpx.AsyncClient | None = None):
        if not access_token:
            raise ValueError("access_token_required")
        self._token = access_token
        self._client = client

    async def _get(self, url: str, params: dict) -> dict:
        if not (url.startswith(DATA_URL + "/") or url == ANALYTICS_URL):
            raise ValueError("provider_url_not_allowlisted")
        try:
            if self._client is not None:
                response = await self._client.get(url, params=params,
                    headers={"Authorization": f"Bearer {self._token}"})
            else:
                async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
                    response = await client.get(url, params=params,
                        headers={"Authorization": f"Bearer {self._token}"})
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            reason = "AUTH_REQUIRED" if status == 401 else (
                "SCOPE_REQUIRED" if status == 403 else (
                    "RATE_LIMITED" if status == 429 else "PROVIDER_ERROR"))
            raise ProviderError(reason) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE_OR_INVALID_RESPONSE") from exc
        if not isinstance(data, dict):
            raise ProviderError("PROVIDER_RESPONSE_INVALID")
        return data

    async def channel(self) -> dict:
        return await self._get(DATA_URL + "/channels", {"part": "snippet,statistics,contentDetails",
                                                        "mine": "true"})

    async def videos(self, max_results: int = 25) -> dict:
        if not 1 <= max_results <= 50:
            raise ValueError("max_results_out_of_range")
        channels = await self.channel()
        items = channels.get("items", [])
        if not items:
            return {"items": [], "pageInfo": {"totalResults": 0}}
        uploads = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads:
            raise ProviderError("UPLOADS_PLAYLIST_UNAVAILABLE")
        return await self._get(DATA_URL + "/playlistItems", {
            "part": "snippet,contentDetails", "playlistId": uploads, "maxResults": max_results
        })

    async def analytics(self, start_date: str, end_date: str) -> dict:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("date_iso_required") from exc
        if end < start or (end - start).days > 366:
            raise ValueError("date_window_invalid")
        return await self._get(ANALYTICS_URL, {
            "ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
            "metrics": "views,estimatedMinutesWatched,subscribersGained", "dimensions": "day",
            "sort": "day",
        })
