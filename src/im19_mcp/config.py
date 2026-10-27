"""Strict environment configuration; never log secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def _https_url(value: str, name: str, *, allow_loopback: bool = False) -> str:
    if any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError(f"{name}: invalid URL")
    url = urlsplit(value)
    localhost = url.hostname in {"127.0.0.1", "localhost", "::1"}
    if not url.hostname or url.username or url.password or url.fragment or url.query:
        raise ValueError(f"{name}: invalid URL")
    try:
        port = url.port
    except ValueError:
        raise ValueError(f"{name}: invalid URL") from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{name}: invalid URL")
    if url.scheme != "https" and not (allow_loopback and localhost and url.scheme == "http"):
        raise ValueError(f"{name}: HTTPS required")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    vault_key: str = field(repr=False)
    audit_salt: str = field(repr=False)
    google_client_id: str
    google_client_secret: str = field(repr=False)
    google_redirect_uri: str
    mcp_issuer: str
    mcp_resource: str
    mcp_introspection: str
    mcp_client_id: str
    mcp_client_secret: str = field(repr=False)
    dev_subject: str
    bind_host: str = "127.0.0.1"
    port: int = 8000
    deployment_environment: str = "production"
    vault_previous_keys: tuple[str, ...] = field(default=(), repr=False)
    browser_authorization: str = ""
    browser_token: str = ""
    browser_client_id: str = ""
    browser_client_secret: str = field(default="", repr=False)
    enable_approved_drive_writes: bool = False

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
            deployment_environment=os.getenv("IM19_DEPLOYMENT_ENV", "production"),
            vault_previous_keys=tuple(
                part.strip()
                for part in os.getenv("IM19_FERNET_PREVIOUS_KEYS", "").split(",")
                if part.strip()
            ),
            browser_authorization=os.getenv("IM19_BROWSER_AUTHORIZATION", ""),
            browser_token=os.getenv("IM19_BROWSER_TOKEN", ""),
            browser_client_id=os.getenv("IM19_BROWSER_CLIENT_ID", ""),
            browser_client_secret=os.getenv("IM19_BROWSER_CLIENT_SECRET", ""),
            enable_approved_drive_writes=os.getenv("IM19_ENABLE_APPROVED_DRIVE_WRITES", "false").lower() == "true",
        )

    def browser_enabled(self) -> bool:
        return all((self.browser_authorization, self.browser_token,
                    self.browser_client_id, self.browser_client_secret))

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
        if self.deployment_environment not in {"development", "staging", "production"}:
            raise ValueError("invalid_deployment_environment")
        browser_fields = (self.browser_authorization, self.browser_token,
                          self.browser_client_id, self.browser_client_secret)
        if any(browser_fields):
            if not self.browser_enabled():
                raise ValueError("browser_oauth_configuration_incomplete")
            _https_url(self.browser_authorization, "IM19_BROWSER_AUTHORIZATION")
            _https_url(self.browser_token, "IM19_BROWSER_TOKEN")
        if self.vault_previous_keys and (
            self.vault_key in self.vault_previous_keys
            or len(set(self.vault_previous_keys)) != len(self.vault_previous_keys)
        ):
            raise ValueError("duplicate_encryption_keys_forbidden")
        if self.enable_approved_drive_writes and not (self.google_enabled() and self.browser_enabled()):
            raise ValueError("approved_drive_writes_require_google_and_browser_oauth")
        if self.deployment_environment == "production":
            # This implementation requires an OFFLINE full rewrap. Production
            # gateways run primary-only after the migration and validation.
            if self.vault_previous_keys:
                raise ValueError("production_legacy_key_fallback_forbidden")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("production_requires_postgresql")
            if self.dev_subject:
                raise ValueError("production_dev_subject_forbidden")
            if len(self.audit_salt) < 32:
                raise ValueError("production_requires_strong_audit_salt")
            google_fields = (
                self.google_client_id, self.google_client_secret,
                self.google_redirect_uri,
            )
            if any(google_fields):
                if not all(google_fields) or not self.google_enabled():
                    raise ValueError("production_google_oauth_incomplete")
                callback = urlsplit(self.google_redirect_uri)
                resource = urlsplit(self.mcp_resource)
                if (
                    callback.scheme != "https"
                    or callback.netloc != resource.netloc
                    or callback.path != "/oauth/google/callback"
                    or callback.query
                ):
                    raise ValueError("production_google_callback_origin_mismatch")
                if not self.browser_enabled():
                    raise ValueError("production_requires_authenticated_browser_linking")

