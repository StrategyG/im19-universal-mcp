"""Online/offline Alembic migrations for IM19."""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from im19_mcp.config import Settings
from im19_mcp.storage import Base
from im19_mcp.governance import approvals  # noqa: F401  register models for metadata
from im19_mcp.governance import write_approval  # noqa: F401  register approval request schema
from im19_mcp.arusia import workflow  # noqa: F401  register episode + task models
from im19_mcp import distributed_quota  # noqa: F401  register rate windows

metadata = Base.metadata
url = Settings.from_env().database_url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
