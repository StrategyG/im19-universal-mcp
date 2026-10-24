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


if __name__ == "__main__":
    main()
