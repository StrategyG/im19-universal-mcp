"""Dedicated trusted browser inspection worker, never a public MCP action.

Run as a restricted non-root service with an egress firewall and dedicated
least-privilege database role. Do NOT mount provider OAuth tokens, browser
profiles, gateway vault encryption key, or arbitrary user files.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from im19_mcp.browser_jobs import BrowserJobError, BrowserJobQueue
from im19_mcp.browser_worker import inspect_page
from im19_mcp.config import Settings
from im19_mcp.storage import Vault


async def run_once(queue: BrowserJobQueue, origins: tuple[str, ...],
                   *, inspector=inspect_page) -> dict:
    job = queue.claim(origins)
    if job is None:
        return {"status": "NO_READY_JOBS"}
    try:
        # Each request uses a new short-lived Chromium context with NO login.
        result = await inspector(job["url"], allowed_origins=origins)
        queue.finish(job["job_id"], job["lease_token"], result)
        return {"status": "COMPLETED", "job_id": job["job_id"]}
    except BrowserJobError:
        return {"status": "STALE_LEASE", "job_id": job["job_id"]}
    except Exception:
        # Never log untrusted web page data, URL parameters or exception text.
        try:
            response = queue.finish(job["job_id"], job["lease_token"], None, failed=True)
            return {"status": response["status"], "job_id": job["job_id"]}
        except BrowserJobError:
            return {"status": "STALE_LEASE", "job_id": job["job_id"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated, read-only IM19 browser inspection worker")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    args = parser.parse_args(argv)
    if not 0.5 <= args.poll_seconds <= 60:
        parser.error("poll_seconds_out_of_range")
    if os.getenv("IM19_BROWSER_WORKER_ACK") != "YES":
        parser.error("trusted_isolated_worker_required")
    cfg = Settings.from_env()
    if not cfg.enable_browser_jobs or not cfg.browser_inspection_origins:
        parser.error("browser_queue_not_configured")
    if not cfg.vault_key:
        parser.error("database_worker_configuration_missing")
    # Vault key is currently required by the existing Vault constructor, but
    # this process does not decrypt provider tokens. Production DB role MUST
    # deny SELECT on credential and OAuth tables; no provider keys are mounted.
    store = Vault(cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt)
    queue = BrowserJobQueue(store)
    try:
        while True:
            result = asyncio.run(run_once(queue, cfg.browser_inspection_origins))
            print(json.dumps(result, sort_keys=True), flush=True)
            if not args.loop:
                return 0 if result["status"] in {"NO_READY_JOBS", "COMPLETED"} else 1
            import time
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
