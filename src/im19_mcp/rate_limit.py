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


class DistributedRateLimitMiddleware:
    """Pre-auth rate admission shared across gateway replicas via PostgreSQL.

    Uses only the trusted ASGI socket peer, never client-controlled HTTP
    forwarding headers. An external HTTPS ingress must enforce per-real-IP
    limits independently when it terminates client connections.
    """

    def __init__(
        self, app, *, quota, requests_per_minute: int = 120,
        exempt_paths: frozenset[str] = frozenset({"/health", "/ready"}),
    ):
        if not 1 <= requests_per_minute <= 100000:
            raise ValueError("invalid_rate_limit")
        self.app = app
        self.quota = quota
        self.limit = requests_per_minute
        self.exempt_paths = exempt_paths

    async def __call__(self, scope, receive, send):
        from starlette.concurrency import run_in_threadpool
        from im19_mcp.distributed_quota import QuotaUnavailable

        if scope["type"] != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        socket_peer = scope.get("client")
        if not isinstance(socket_peer, (tuple, list)) or not socket_peer or not socket_peer[0]:
            response = JSONResponse(
                {"error": "QUOTA_UNAVAILABLE"}, status_code=503,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        # No Forwarded/X-Forwarded-For trust: the immediate socket peer is a
        # trustworthy ingress identity but may represent many end users.
        try:
            allowed, retry = await run_in_threadpool(
                self.quota.consume, "http:peer", str(socket_peer[0]),
                limit=self.limit, window_seconds=60,
            )
        except (QuotaUnavailable, ValueError):
            response = JSONResponse(
                {"error": "QUOTA_UNAVAILABLE"}, status_code=503,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        if not allowed:
            response = JSONResponse(
                {"error": "RATE_LIMITED"}, status_code=429,
                headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
