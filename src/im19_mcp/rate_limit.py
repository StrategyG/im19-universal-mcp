"""Bounded per-process HTTP throttling for staging.

Production requires distributed rate limits at ingress (and per-user quotas).
Never trust X-Forwarded-For unless a reverse proxy is explicitly trusted.
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

from starlette.responses import JSONResponse


class RateLimitMiddleware:
    def __init__(self, app, *, requests_per_minute: int = 120, max_clients: int = 4096):
        if requests_per_minute < 1 or max_clients < 1:
            raise ValueError("invalid_rate_limit")
        self.app = app
        self.limit = requests_per_minute
        self.max_clients = max_clients
        self.buckets: dict[str, deque[float]] = {}
        self.lock = Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        key = str(client[0]) if client else "unknown"
        now = time.monotonic()
        with self.lock:
            bucket = self.buckets.get(key)
            if bucket is None:
                if len(self.buckets) >= self.max_clients:
                    threshold = now - 60
                    self.buckets = {
                        ip: history for ip, history in self.buckets.items()
                        if history and history[-1] > threshold
                    }
                if len(self.buckets) >= self.max_clients:
                    bucket = None
                else:
                    bucket = deque()
                    self.buckets[key] = bucket
            if bucket is None:
                allowed = False
            else:
                cutoff = now - 60
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                allowed = len(bucket) < self.limit
                if allowed:
                    bucket.append(now)
        if not allowed:
            response = JSONResponse({"error": "RATE_LIMITED"}, status_code=429,
                                    headers={"Retry-After": "60", "Cache-Control": "no-store"})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
