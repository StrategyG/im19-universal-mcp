"""Container readiness probe: loopback only, no proxy or credential output."""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from im19_mcp.config import Settings


def main() -> int:
    try:
        cfg = Settings.from_env()
        authority = urlsplit(cfg.mcp_resource).netloc
        if not authority:
            return 1
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{cfg.port}/ready", headers={"Host": authority})
        return 0 if response.status_code == 200 and response.json() == {"status": "ready"} else 1
    except (httpx.HTTPError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
