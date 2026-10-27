"""Strict origin policy for the opt-in, isolated IM19 browser prototype.

This is not a replacement for container/network egress controls. Never expose
this prototype as an MCP tool without adding authenticated per-user policy.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


class BrowserPolicyError(ValueError):
    """A browser URL or allowlist entry failed closed."""


def _origin(value: str, *, allow_path: bool) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise BrowserPolicyError("invalid_browser_url")
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise BrowserPolicyError("invalid_browser_url")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise BrowserPolicyError("invalid_browser_url") from None
    if (parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or port not in (None, 443)
            or parsed.fragment or not allow_path and (parsed.path not in ("", "/") or parsed.query)):
        raise BrowserPolicyError("https_origin_required")
    if host.endswith(".") or len(host) > 253 or host != host.encode("ascii", errors="ignore").decode("ascii"):
        raise BrowserPolicyError("invalid_browser_host")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        raise BrowserPolicyError("private_browser_host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BrowserPolicyError("ip_literal_not_allowed")
    labels = host.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                              for label in labels):
        raise BrowserPolicyError("invalid_browser_host")
    return "https://" + host


@dataclass(frozen=True)
class BrowserPolicy:
    """Exact HTTPS origins. No implicit wildcard or subdomain grants."""

    allowed_origins: frozenset[str]

    @classmethod
    def from_origins(cls, origins: list[str] | tuple[str, ...]) -> "BrowserPolicy":
        if not isinstance(origins, (tuple, list)) or not 1 <= len(origins) <= 12:
            raise BrowserPolicyError("browser_allowlist_required")
        normalized = frozenset(_origin(value, allow_path=False) for value in origins)
        return cls(allowed_origins=normalized)

    def require(self, url: str) -> str:
        destination = _origin(url, allow_path=True)
        if destination not in self.allowed_origins:
            raise BrowserPolicyError("origin_not_allowed")
        return destination
