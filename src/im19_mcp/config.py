"""Strict environment configuration; never log secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def _https_url(value: str, name: str, *, allow_loopback: bool = False) -> str:
    url = urlsplit(value)
    localhost = url.hostname in {"127.0.0.1", "localhost", "::1"}
    if not url.hostname or url.username or url.password or url.fragment:
        raise ValueError(f"{name}: invalid URL")
    if url.scheme != "https" and not (allow_loopback and localhost and url.scheme == "http"):
        raise ValueError(f"{name}: HTTPS required")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    vault_key: str
    audit_salt: str
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    mcp_issuer: str
    mcp_resource: str
    mcp_introspection: str
    mcp_client_id: str
    mcp_client_secret: str
    dev_subject: str
    bind_host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("IM19_DATABASE_URL", "sqlite:///./im19-dev.sqlite3"),
            vault_key=os.getenv("IM19_FERNET_KEY", ""),
            audit_salt=os.getenv("IM19_AUDIT_SALT", ""),
            google_client_id=os.getenv("IM19_GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.getenv("IM19_GOOGLE_CLIENT_SECRET", ""),
            google_redirect_uri=os.getenv("IM19_GOOGLE_REDIRECT_URI", ""),
            mcp_issuer=os.getenv("IM19_MCP_ISSUER", ""),
            mcp_resource=os.getenv("IM19_MCP_RESOURCE", ""),
            mcp_introspection=os.getenv("IM19_MCP_INTROSPECTION", ""),
            mcp_client_id=os.getenv("IM19_MCP_CLIENT_ID", ""),
            mcp_client_secret=os.getenv("IM19_MCP_CLIENT_SECRET", ""),
            dev_subject=os.getenv("IM19_DEV_SUBJECT", ""),
            bind_host=os.getenv("IM19_BIND_HOST", "127.0.0.1"),
            port=int(os.getenv("IM19_PORT", "8000")),
        )

    def google_enabled(self) -> bool:
        values = (self.google_client_id, self.google_client_secret, self.google_redirect_uri, self.vault_key)
        if not all(values):
            return False
        _https_url(self.google_redirect_uri, "IM19_GOOGLE_REDIRECT_URI", allow_loopback=True)
        return True

    def require_remote(self) -> None:
        fields = (self.mcp_issuer, self.mcp_resource, self.mcp_introspection,
                  self.mcp_client_id, self.mcp_client_secret, self.audit_salt,
                  self.vault_key)
        if not all(fields):
            raise ValueError("remote_auth_and_encryption_configuration_required")
        for name, value in (("issuer", self.mcp_issuer), ("resource", self.mcp_resource),
                            ("introspection", self.mcp_introspection)):
            _https_url(value, name)
        if urlsplit(self.mcp_resource).path != "/mcp":
            raise ValueError("mcp_resource_must_end_with_/mcp")
        if not 1 <= self.port <= 65535:
            raise ValueError("invalid_port")
