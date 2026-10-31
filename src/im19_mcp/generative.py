"""Provider-neutral, tenant-scoped gateway to an operator-managed inference worker.

IM19 orchestrates generation jobs; it does not pretend that an inference model or GPU
is bundled in this repository. The worker implements the documented HTTP contract.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

STAGES = ("text_to_image", "image_to_3d", "3d_to_motion", "render_video", "full_pipeline")
_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ALLOWED_PARAMS = {"width", "height", "seed", "steps", "guidance", "fps", "frames", "quality"}
_STATUS = {"queued", "accepted", "running", "succeeded", "failed", "cancelled"}


class GenerativeError(ValueError):
    """Safe, non-secret generation gateway error."""


@dataclass(frozen=True)
class GenerativeSettings:
    base_url: str
    api_key: str
    enabled: bool
    allowed_profiles: tuple[str, ...]
    principal_salt: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, *, principal_salt: str) -> "GenerativeSettings":
        enabled = os.getenv("IM19_GENERATIVE_ENABLED", "false").strip().lower() == "true"
        base_url = os.getenv("IM19_GENERATIVE_WORKER_URL", "").strip()
        api_key = os.getenv("IM19_GENERATIVE_WORKER_TOKEN", "")
        profiles_raw = os.getenv("IM19_GENERATIVE_PROFILES", "default")
        profiles = tuple(p.strip() for p in profiles_raw.split(",") if p.strip())
        if enabled:
            parsed = urlsplit(base_url)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))):
                raise ValueError("generative_worker_url_invalid")
            if not api_key or len(api_key) < 32 or len(api_key) > 8192:
                raise ValueError("generative_worker_token_invalid")
            if not profiles or len(profiles) > 32 or any(not _PROFILE_ID.fullmatch(p) for p in profiles):
                raise ValueError("generative_profiles_invalid")
            if not principal_salt or len(principal_salt) < 32:
                raise ValueError("generative_principal_salt_required")
        return cls(base_url.rstrip("/"), api_key, enabled, profiles, principal_salt)


class GenerativeClient:
    """Small HTTP client for an isolated inference worker; never accepts caller URLs."""

    def __init__(self, settings: GenerativeSettings, *, http_client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._http = http_client

    def _principal(self, subject: str) -> str:
        return hmac.new(self.settings.principal_salt.encode(), subject.encode(), hashlib.sha256).hexdigest()

    async def _request(self, method: str, path: str, *, subject: str, payload: dict | None = None) -> dict:
        if not self.settings.enabled:
            raise GenerativeError("GENERATION_DISABLED")
        if not subject or len(subject) > 255:
            raise GenerativeError("AUTH_REQUIRED")
        own_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=httpx.Timeout(self.settings.timeout_seconds))
        try:
            response = await client.request(
                method, self.settings.base_url + path,
                headers={
                    "Authorization": "Bearer " + self.settings.api_key,
                    "X-IM19-Principal": self._principal(subject),
                    "X-IM19-Request-ID": secrets.token_urlsafe(18),
                    "Accept": "application/json",
                },
                json=payload,
            )
            if response.status_code in (401, 403):
                raise GenerativeError("WORKER_AUTH_FAILED")
            if response.status_code == 429:
                raise GenerativeError("WORKER_RATE_LIMITED")
            if response.status_code >= 500:
                raise GenerativeError("WORKER_UNAVAILABLE")
            if response.status_code >= 400:
                raise GenerativeError("WORKER_REQUEST_REJECTED")
            try:
                data = response.json()
            except (ValueError, json.JSONDecodeError):
                raise GenerativeError("WORKER_INVALID_RESPONSE") from None
            if not isinstance(data, dict):
                raise GenerativeError("WORKER_INVALID_RESPONSE")
            return data
        except httpx.TimeoutException:
            raise GenerativeError("WORKER_TIMEOUT") from None
        except httpx.HTTPError:
            raise GenerativeError("WORKER_UNAVAILABLE") from None
        finally:
            if own_client:
                await client.aclose()

    async def capabilities(self, *, subject: str) -> dict:
        data = await self._request("GET", "/v1/capabilities", subject=subject)
        stages = data.get("stages")
        if not isinstance(stages, list):
            raise GenerativeError("WORKER_INVALID_RESPONSE")
        safe = [s for s in stages if isinstance(s, str) and s in STAGES]
        return {"status": "OK", "stages": safe, "profiles": list(self.settings.allowed_profiles)}

    async def submit(
        self, *, subject: str, stage: str, profile: str, prompt: str,
        input_asset_ids: list[str], parameters: dict[str, Any], confirm: bool,
    ) -> dict:
        if not confirm:
            return {"status": "CONFIRMATION_REQUIRED", "required": "confirm=true"}
        if stage not in STAGES:
            return {"status": "INVALID_ARGUMENT", "reason": "unsupported_stage"}
        if profile not in self.settings.allowed_profiles:
            return {"status": "DENY", "reason": "profile_not_allowlisted"}
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            return {"status": "INVALID_ARGUMENT", "reason": "prompt_must_be_1_to_4000_characters"}
        if not isinstance(input_asset_ids, list) or len(input_asset_ids) > 8:
            return {"status": "INVALID_ARGUMENT", "reason": "input_asset_ids_limit"}
        if any(not isinstance(x, str) or not _ASSET_ID.fullmatch(x) for x in input_asset_ids):
            return {"status": "INVALID_ARGUMENT", "reason": "invalid_asset_id"}
        if not isinstance(parameters, dict) or set(parameters) - _ALLOWED_PARAMS:
            return {"status": "INVALID_ARGUMENT", "reason": "unsupported_generation_parameter"}
        for key in ("width", "height"):
            if key in parameters and (type(parameters[key]) is not int or not 256 <= parameters[key] <= 4096):
                return {"status": "INVALID_ARGUMENT", "reason": key + "_out_of_range"}
        if "seed" in parameters and (type(parameters["seed"]) is not int or not 0 <= parameters["seed"] <= 2**32 - 1):
            return {"status": "INVALID_ARGUMENT", "reason": "seed_out_of_range"}
        if "steps" in parameters and (type(parameters["steps"]) is not int or not 1 <= parameters["steps"] <= 100):
            return {"status": "INVALID_ARGUMENT", "reason": "steps_out_of_range"}
        if "fps" in parameters and (type(parameters["fps"]) is not int or not 1 <= parameters["fps"] <= 60):
            return {"status": "INVALID_ARGUMENT", "reason": "fps_out_of_range"}
        if "frames" in parameters and (type(parameters["frames"]) is not int or not 1 <= parameters["frames"] <= 1800):
            return {"status": "INVALID_ARGUMENT", "reason": "frames_out_of_range"}
        data = await self._request("POST", "/v1/jobs", subject=subject, payload={
            "stage": stage, "profile": profile, "prompt": prompt.strip(),
            "input_asset_ids": input_asset_ids, "parameters": parameters,
        })
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise GenerativeError("WORKER_INVALID_RESPONSE")
        return {"status": "ACCEPTED", "job_id": job_id, "stage": stage, "profile": profile,
                "note": "Accepted by the configured inference worker; completion is not implied."}

    async def get_job(self, *, subject: str, job_id: str) -> dict:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            return {"status": "INVALID_ARGUMENT", "reason": "invalid_job_id"}
        data = await self._request("GET", "/v1/jobs/" + job_id, subject=subject)
        state = data.get("status")
        if state not in _STATUS:
            raise GenerativeError("WORKER_INVALID_RESPONSE")
        result: dict[str, Any] = {"status": state, "job_id": job_id}
        stage = data.get("stage")
        if isinstance(stage, str) and stage in STAGES:
            result["stage"] = stage
        progress = data.get("progress")
        if isinstance(progress, (int, float)) and 0 <= progress <= 1:
            result["progress"] = progress
        assets = data.get("output_asset_ids", [])
        if isinstance(assets, list) and len(assets) <= 16 and all(
            isinstance(x, str) and _ASSET_ID.fullmatch(x) for x in assets
        ):
            result["output_asset_ids"] = assets
        if state == "failed":
            error_code = data.get("error_code")
            if isinstance(error_code, str) and re.fullmatch(r"[A-Z0-9_]{1,64}", error_code):
                result["error_code"] = error_code
        return result



def register_generative_tools(
    server, *, settings, subject_provider, audit, quota,
) -> None:
    """Register authenticated, audited generation tools on the existing MCP server."""
    from im19_mcp.generative import GenerativeClient, GenerativeError, GenerativeSettings

    try:
        generation_settings = GenerativeSettings.from_env(principal_salt=settings.audit_salt)
        client = GenerativeClient(generation_settings)
        configuration_error = None
    except ValueError:
        client = None
        configuration_error = "generative_configuration_invalid"

    def unavailable() -> dict:
        if configuration_error:
            return {"status": "NOT_CONFIGURED", "reason": configuration_error}
        return {"status": "NOT_CONFIGURED", "reason": "generation_worker_not_enabled"}

    @server.tool(name="generative_get_capabilities")
    async def generative_get_capabilities() -> dict:
        """List multimodal generation stages exposed by the configured worker."""
        user = subject_provider()
        if not user:
            return {"status": "AUTH_REQUIRED", "reason": "verified_mcp_identity_required"}
        if client is None or not client.settings.enabled:
            return unavailable()
        denied = await quota(user, "mcp:generative-capabilities", limit=30)
        if denied is not None:
            return denied
        try:
            result = await client.capabilities(subject=user)
            if not await audit(user, "generative", "capabilities", "SUCCESS"):
                return {"status": "SERVICE_UNAVAILABLE", "reason": "audit_unavailable"}
            return result
        except GenerativeError as exc:
            await audit(user, "generative", "capabilities", "ERROR")
            return {"status": "ERROR", "error_code": str(exc)}

    @server.tool(name="generative_submit_job")
    async def generative_submit_job(
        stage: str, profile: str, prompt: str,
        input_asset_ids: list[str] | None = None,
        parameters: dict | None = None, confirm: bool = False,
    ) -> dict:
        """Submit a confirmed job: text-to-image, image-to-3D, 3D motion, or video."""
        user = subject_provider()
        if not user:
            return {"status": "AUTH_REQUIRED", "reason": "verified_mcp_identity_required"}
        if client is None or not client.settings.enabled:
            return unavailable()
        if not confirm:
            return {"status": "CONFIRMATION_REQUIRED", "required": "confirm=true"}
        denied = await quota(user, "mcp:generative-submit", limit=5)
        if denied is not None:
            return denied
        if not await audit(user, "generative", "submit", "START"):
            return {"status": "SERVICE_UNAVAILABLE", "reason": "audit_unavailable"}
        try:
            result = await client.submit(
                subject=user, stage=stage, profile=profile, prompt=prompt,
                input_asset_ids=input_asset_ids or [], parameters=parameters or {},
                confirm=confirm,
            )
            if not await audit(user, "generative", "submit", "SUCCESS"):
                return {"status": "SERVICE_UNAVAILABLE", "reason": "audit_unavailable"}
            return result
        except GenerativeError as exc:
            await audit(user, "generative", "submit", "ERROR")
            return {"status": "ERROR", "error_code": str(exc)}

    @server.tool(name="generative_get_job")
    async def generative_get_job(job_id: str) -> dict:
        """Read a job owned by the authenticated subject; the worker enforces tenant scope."""
        user = subject_provider()
        if not user:
            return {"status": "AUTH_REQUIRED", "reason": "verified_mcp_identity_required"}
        if client is None or not client.settings.enabled:
            return unavailable()
        denied = await quota(user, "mcp:generative-job-status", limit=60)
        if denied is not None:
            return denied
        try:
            result = await client.get_job(subject=user, job_id=job_id)
            if not await audit(user, "generative", "get_job", "SUCCESS"):
                return {"status": "SERVICE_UNAVAILABLE", "reason": "audit_unavailable"}
            return result
        except GenerativeError as exc:
            await audit(user, "generative", "get_job", "ERROR")
            return {"status": "ERROR", "error_code": str(exc)}
