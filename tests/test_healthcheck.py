import httpx
import pytest

from im19_mcp import healthcheck


@pytest.mark.parametrize("status,body,exit_code", [
    (200, b'{"status":"ready"}', 0), (503, b'{"status":"unavailable"}', 1),
    (200, b'{}', 1), (200, b'broken', 1),
])
def test_container_probe_checks_readiness_over_loopback(remote_cfg, monkeypatch, status, body, exit_code):
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(status, content=body)

    client = httpx.Client(transport=httpx.MockTransport(reply))
    monkeypatch.setattr(healthcheck.Settings, "from_env", lambda: remote_cfg)
    monkeypatch.setattr(healthcheck.httpx, "Client", lambda **kwargs: client)
    assert healthcheck.main() == exit_code
    assert requests[0].url.host == "127.0.0.1"
    assert requests[0].url.path == "/ready"
    assert requests[0].headers["Host"] == "mcp.test"
    assert "Authorization" not in requests[0].headers
