"""Atomic shared rate windows for gateway+authenticated MCP quotas.

Revision ID: 0005_distributed_quotas
Revises: 0004_governance
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_distributed_quotas"
down_revision = "0004_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "distributed_rate_windows",
        sa.Column("key_digest", sa.String(64), primary_key=True),
        sa.Column("bucket_start", sa.BigInteger(), primary_key=True),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_distributed_rate_windows_expires", "distributed_rate_windows", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_distributed_rate_windows_expires", table_name="distributed_rate_windows")
    op.drop_table("distributed_rate_windows")
