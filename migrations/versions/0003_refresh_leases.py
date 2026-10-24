"""Add cross-worker refresh lease with compare-and-set save guard.

Revision ID: 0003_refresh_leases
Revises: 0002_pending_links
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_refresh_leases"
down_revision = "0002_pending_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oauth_credentials",
        sa.Column("refresh_owner", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "oauth_credentials",
        sa.Column("refresh_until", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("oauth_credentials", "refresh_until")
    op.drop_column("oauth_credentials", "refresh_owner")
