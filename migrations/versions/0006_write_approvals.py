"""Browser-authenticated write approval requests; no auto-approval.

Revision ID: 0006_write_approval_requests
Revises: 0005_distributed_quotas
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_write_approvals"
down_revision = "0005_distributed_quotas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_approval_requests",
        sa.Column("ticket_hash", sa.String(64), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("name_cipher", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("oauth_state_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("verifier_cipher", sa.Text(), nullable=True),
        sa.Column("browser_hash", sa.String(64), nullable=True),
        sa.Column("csrf_hash", sa.String(64), nullable=True),
        sa.Column("approval_cipher", sa.Text(), nullable=True),
    )
    op.create_index("ix_write_approval_requests_expiration",
                    "write_approval_requests", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_write_approval_requests_expiration", table_name="write_approval_requests")
    op.drop_table("write_approval_requests")
