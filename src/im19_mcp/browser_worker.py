"""Opt-in, read-only Chromium inspection worker (NOT exposed via MCP).

Requires the optional Playwright extra and its browser binary. No persistent
browser profile, cookie export, automated login, uploads, or provider writes.
Production usage also requires network-level egress restrictions and identity
bound job authorization in a dedicated isolated worker container.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from typing import Any

from im19_mcp.browser_policy import BrowserPolicy, BrowserPolicyError


class BrowserDependencyError(RuntimeError):
    """The separately installed browser runtime is unavailable."""


async def inspect_page(
    url: str, *,
    allowed_origins: list[str] | tuple[str, ...],
    timeout_ms: int = 10000,
    engine_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Inspect an explicitly approved HTTPS page in an ephemeral Chromium context.

    Browser requests outside the exact allowlist are aborted, including
    redirected navigations and subresources. All websocket attempts are
    blocked. This method returns only sanitized metadata, not page HTML.
    """
    policy = BrowserPolicy.from_origins(allowed_origins)
    policy.require(url)
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1000 <= timeout_ms <= 30000:
        raise BrowserPolicyError("invalid_browser_timeout")
    if engine_factory is None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserDependencyError("playwright_not_installed") from None
        engine_factory = async_playwright

    blocked = 0
    async with engine_factory() as playwright:
        browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = await browser.new_context(
                accept_downloads=False,
                service_workers="block",
                ignore_https_errors=False,
                permissions=[],
            )
            async def guard_request(route: Any) -> None:
                nonlocal blocked
                try:
                    policy.require(route.request.url)
                except BrowserPolicyError:
                    blocked += 1
                    await route.abort()
                else:
                    await route.continue_()

            async def block_websocket(socket: Any) -> None:
                nonlocal blocked
                blocked += 1
                await socket.close()

            await context.route("**/*", guard_request)
            # Playwright 1.48+: route websockets as well as HTTP requests.
            if not hasattr(context, "route_web_socket"):
                raise BrowserDependencyError("websocket_isolation_unavailable")
            await context.route_web_socket("**/*", block_websocket)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            final_origin = policy.require(page.url)
            title = await page.title()
            return {
                "status": "INSPECTED",
                "origin": final_origin,
                "title": title[:200] if isinstance(title, str) else "",
                "blocked_requests": blocked,
                "live_provider_connection_verified": False,
            }
        finally:
            await browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in, read-only browser inspection. Not an MCP endpoint or production service."
    )
    parser.add_argument("url")
    parser.add_argument("--allow-origin", action="append", required=True,
                        help="Exact HTTPS origin; may be provided more than once.")
    parser.add_argument("--timeout-ms", type=int, default=10000)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(inspect_page(
            args.url, allowed_origins=args.allow_origin, timeout_ms=args.timeout_ms,
        ))
    except (BrowserPolicyError, BrowserDependencyError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 1
    except Exception:
        # Browser/website exceptions may contain query tokens or other secrets.
        print(json.dumps({"status": "FAILED", "reason": "browser_runtime_failed"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
