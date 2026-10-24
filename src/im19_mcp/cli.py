"""Explicit local bootstrap; use Alembic migrations for production."""
from __future__ import annotations

from im19_mcp.config import Settings
from im19_mcp.storage import Vault


def main() -> None:
    cfg = Settings.from_env()
    if not cfg.vault_key:
        raise SystemExit("IM19_FERNET_KEY is required")
    Vault(
        cfg.database_url, encryption_key=cfg.vault_key,
        audit_salt=cfg.audit_salt, previous_keys=cfg.vault_previous_keys,
    ).initialize_schema()
    print("IM19 local schema initialized; use migrations for production.")


def purge_expired() -> None:
    """Trusted operations command for expired OAuth state / token retention."""
    import json

    cfg = Settings.from_env()
    if not cfg.vault_key:
        raise SystemExit("IM19_FERNET_KEY is required")
    store = Vault(
        cfg.database_url, encryption_key=cfg.vault_key, audit_salt=cfg.audit_salt,
        previous_keys=cfg.vault_previous_keys,
    )
    # Never output subjects, codes, OAuth tokens or ciphertext.
    expired = store.purge_expired()
    from im19_mcp.governance import ApprovalLedger

    expired["governance_approvals"] = ApprovalLedger(store).purge_expired_approvals()
    print(json.dumps(expired, sort_keys=True))



def rotate_keys() -> None:
    """Offline-only key migration. Never expose via MCP/HTTP actions."""
    import json
    import os

    # The operator must stop ALL gateway and background writer processes first.
    # A truthful lockout cannot be inferred from an environment variable alone.
    if os.getenv("IM19_OFFLINE_MAINTENANCE") != "YES":
        raise SystemExit("offline_maintenance_confirmation_required")
    cfg = Settings.from_env()
    if not cfg.vault_key or not cfg.vault_previous_keys:
        raise SystemExit("primary_and_previous_fernet_keys_required")
    if cfg.deployment_environment == "production" and not cfg.database_url.startswith(
        "postgresql+psycopg://"
    ):
        raise SystemExit("production_requires_postgresql")

    vault = Vault(
        cfg.database_url, encryption_key=cfg.vault_key,
        audit_salt=cfg.audit_salt, previous_keys=cfg.vault_previous_keys,
    )
    # A DB backup and external write outage are operator prerequisites.
    counts = vault.rotate_encryption(offline_maintenance_ack=True)
    print(json.dumps({"status": "ROTATED_AND_VERIFIED", "counts": counts}, sort_keys=True))


def verify_primary_key() -> None:
    """Check primary-only decryptability without retaining legacy key fallback."""
    import json

    cfg = Settings.from_env()
    if not cfg.vault_key:
        raise SystemExit("IM19_FERNET_KEY is required")
    vault = Vault(cfg.database_url, encryption_key=cfg.vault_key,
                  audit_salt=cfg.audit_salt)
    counts = vault.verify_primary_key_coverage()
    print(json.dumps({"status": "PRIMARY_ONLY_VERIFIED", "counts": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
