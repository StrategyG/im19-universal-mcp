"""Operational readiness: a healthy HTTP process is not a ready MCP service.

Exposes no DB credentials, schema names, provider tokens or detailed errors.
"""
from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

REQUIRED_SCHEMA_REVISION = "0004_governance"


def database_ready(engine: Engine, required_revision: str = REQUIRED_SCHEMA_REVISION) -> bool:
    """Require the database to be reachable and fully migrated."""
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
            return revision == required_revision
    except (SQLAlchemyError, OSError, ValueError):
        return False
