"""Security and protocol-boundary tests for optional browser foundation.

All browser objects are synthetic: live Chromium/ARM64 acceptance is separate.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from im19_mcp.browser_policy import BrowserPolicy, BrowserPolicyError
from im19_mcp.browser_worker import inspect_page, main


@pytest.mark.parametrize("bad", [
    "http://example.com", "file:///etc/passwd", "javascript:alert(1)",
    "https://localhost/", "https://localhost.localdomain",
    "https://127.0.0.1/", "https://[::1]/", "https://example.local/",
    "https://example.internal/", "https://user:pass@example.com/",
    "https://example.com:4443/", "https://example.com.evil.test/",
    "https://example.com./", "https://example.com/#fragment",
    "https://example.com/\nsecret", "https://example.com@evil.test/",
    "https://xn--e1afmkfd.example/",
])
def test_origin_policy_denies_unsafe_or_unlisted_destinations(bad):
    policy = BrowserPolicy.from_origins(["https://example.com"])
    with pytest.raises(BrowserPolicyError):
        policy.require(bad)


@pytest.mark.parametrize("bad", [
    "https://example.com/path", "https://*.example.com", "https://example.com:8443",
    "http://example.com", "https://localhost", "https://127.0.0.1", "https://one",
])
def test_allowlist_must_contain_only_exact_public_https_origins(bad):
    with pytest.raises(BrowserPolicyError):
        BrowserPolicy.from_origins([bad])


def test_approved_paths_and_exact_subdomain_require_explicit_origin():
    policy = BrowserPolicy.from_origins(["https://example.com", "https://cdn.example.com"])
    assert policy.require("https://example.com/path?key=not-logged") == "https://example.com"
    assert policy.require("https://cdn.example.com/x") == "https://cdn.example.com"
    with pytest.raises(BrowserPolicyError):
        policy.require("https://unlisted.example.com/x")


@pytest.mark.parametrize("origins", [[], ["https://example.com"] * 13, ["http://example.com"]])
def test_allowlist_fail_closed(origins):
    with pytest.raises(BrowserPolicyError):
        BrowserPolicy.from_origins(origins)


class FakeRoute:
    def __init__(self, url):
        self.request = SimpleNamespace(url=url)
        self.allowed = False
        self.blocked = False

    async def abort(self):
        self.blocked = True

    async def continue_(self):
        self.allowed = True


class FakeSocket:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakePage:
    def __init__(self, context, final_url):
        self.context = context
        self.url = final_url
        self.saw_allowed = False
        self.saw_denied = False
        self.socket_denied = False

    async def goto(self, url, **kwargs):
        allowed = FakeRoute(url)
        await self.context.http_callback(allowed)
        self.saw_allowed = allowed.allowed
        blocked = FakeRoute("https://untrusted.example.net/tracker")
        await self.context.http_callback(blocked)
        self.saw_denied = blocked.blocked
        ws = FakeSocket()
        await self.context.ws_callback(ws)
        self.socket_denied = ws.closed

    async def title(self):
        return "Test title"


class FakeContext:
    def __init__(self, final_url):
        self.final_url = final_url
        self.options = None
        self.page = None

    async def route(self, pattern, callback):
        assert pattern == "**/*"
        self.http_callback = callback

    async def route_web_socket(self, pattern, callback):
        assert pattern == "**/*"
        self.ws_callback = callback

    async def new_page(self):
        self.page = FakePage(self, self.final_url)
        return self.page


class FakeBrowser:
    def __init__(self, final_url):
        self.context = FakeContext(final_url)
        self.closed = False

    async def new_context(self, **kwargs):
        self.context.options = kwargs
        return self.context

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, final_url):
        self.browser = FakeBrowser(final_url)

    async def launch(self, **kwargs):
        assert kwargs == {"headless": True, "chromium_sandbox": True}
        return self.browser


class FakeEngine:
    def __init__(self, final_url):
        self.chromium = FakeChromium(final_url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_worker_inspection_is_ephemeral_and_blocks_cross_origin_requests():
    engine = FakeEngine("https://example.com/result")
    result = await inspect_page(
        "https://example.com/start",
        allowed_origins=["https://example.com"],
        engine_factory=lambda: engine,
    )
    browser = engine.chromium.browser
    page = browser.context.page
    assert result == {
        "status": "INSPECTED", "origin": "https://example.com",
        "title": "Test title", "blocked_requests": 2,
        "live_provider_connection_verified": False,
    }
    assert page.saw_allowed and page.saw_denied and page.socket_denied
    assert browser.closed
    assert browser.context.options["accept_downloads"] is False
    assert browser.context.options["service_workers"] == "block"
    assert browser.context.options["ignore_https_errors"] is False


@pytest.mark.asyncio
async def test_redirect_to_unapproved_origin_fails_closed_and_closes_browser():
    engine = FakeEngine("https://evil.example.net/landing")
    with pytest.raises(BrowserPolicyError, match="origin_not_allowed"):
        await inspect_page(
            "https://example.com/start", allowed_origins=["https://example.com"],
            engine_factory=lambda: engine,
        )
    assert engine.chromium.browser.closed


@pytest.mark.asyncio
async def test_invalid_url_never_starts_browser():
    def fail_start():
        pytest.fail("browser must never start")
    with pytest.raises(BrowserPolicyError):
        await inspect_page(
            "https://127.0.0.1/secrets", allowed_origins=["https://example.com"],
            engine_factory=fail_start,
        )


def test_cli_does_not_echo_input_url_or_secret_query(capsys):
    assert main(["https://evil.example.net/?token=SENSITIVE-TOKEN",
                 "--allow-origin", "https://example.com"]) == 1
    output = capsys.readouterr().out
    assert "SENSITIVE-TOKEN" not in output
    assert '"status": "BLOCKED"' in output
