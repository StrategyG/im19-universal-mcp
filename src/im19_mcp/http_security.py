"""Apply origin/host validation and browser protections to every HTTP route."""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse


class GatewaySecurityMiddleware:
    def __init__(self, app, *, resource: str, production: bool):
        self.app = app
        url = urlsplit(resource)
        authority = url.netloc.lower()
        self.hosts = {authority}
        if url.port in {None, 443}:
            self.hosts |= {url.hostname.lower(), url.hostname.lower() + ":443"}
        self.origins = {"https://" + host for host in self.hosts}
        self.production = production

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def protected(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Cache-Control", "no-store")
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Content-Security-Policy"] = (
                    "default-src 'none'; frame-ancestors 'none'; form-action 'none'"
                )
                if self.production:
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        hosts = [value.decode("latin-1").lower() for key, value in scope["headers"] if key == b"host"]
        origins = [value.decode("latin-1") for key, value in scope["headers"] if key == b"origin"]
        if len(hosts) != 1 or hosts[0] not in self.hosts:
            await JSONResponse({"error": "INVALID_HOST"}, status_code=421)(scope, receive, protected)
            return
        if len(origins) > 1 or (origins and origins[0] not in self.origins):
            await JSONResponse({"error": "INVALID_ORIGIN"}, status_code=403)(scope, receive, protected)
            return
        if len(scope.get("query_string", b"")) > 8192:
            await JSONResponse({"error": "REQUEST_TOO_LARGE"}, status_code=414)(scope, receive, protected)
            return
        await self.app(scope, receive, protected)
