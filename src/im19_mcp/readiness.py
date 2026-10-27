"""Operational readiness: a healthy HTTP process is not a ready MCP service.

Exposes no DB credentials, schema names, provider tokens or detailed errors.
"""
from __future__ import annotations

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import SQLAlchemyError

REQUIRED_SCHEMA_REVISION = "0008_arusia_artifacts"


def database_ready(engine: Engine, required_revision: str = REQUIRED_SCHEMA_REVISION) -> bool:
    """Require the database to be reachable and fully migrated."""
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
            if revision != required_revision:
                return False
            from im19_mcp.storage import Base
            from im19_mcp.governance import approvals  # noqa: F401
            from im19_mcp import distributed_quota  # noqa: F401
            from im19_mcp.governance import write_approval  # noqa: F401
            from im19_mcp.arusia import workflow  # noqa: F401
            from im19_mcp.arusia import artifacts  # noqa: F401

            # A revision marker alone cannot prove tables/columns are usable.
            for table in Base.metadata.sorted_tables:
                connection.execute(select(table).limit(0))
            return True
    except (SQLAlchemyError, OSError, ValueError):
        return False

