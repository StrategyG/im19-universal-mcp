"""Add one-time OAuth confirmation vault.

Revision ID: 0002_pending_links
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_pending_links"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_pending_links",
        sa.Column("confirmation_hash", sa.String(64), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_oauth_pending_links_expires_at", "oauth_pending_links", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_pending_links_expires_at", table_name="oauth_pending_links")
    op.drop_table("oauth_pending_links")
