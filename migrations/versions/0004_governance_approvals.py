"""Persistent one-use human approvals and idempotency receipts.

Revision ID: 0004_governance
Revises: 0003_refresh_leases
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_governance"
down_revision = "0003_refresh_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "governance_approvals",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.String(512), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("approver", sa.String(255), nullable=False),
        sa.Column("evidence_reference", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_governance_approvals_expiry",
        "governance_approvals", ["expires_at"],
    )
    op.create_table(
        "governance_mutation_receipts",
        sa.Column("reservation_id", sa.String(64), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.String(512), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.UniqueConstraint(
            "subject", "provider", "account_id", "action", "idempotency_key",
            name="uq_governance_mutation_identity",
        ),
    )


def downgrade() -> None:
    # Destructive rollback of governance receipts eliminates replay protection;
    # must NOT be run on a production database with real mutations.
    op.drop_table("governance_mutation_receipts")
    op.drop_index("ix_governance_approvals_expiry", table_name="governance_approvals")
    op.drop_table("governance_approvals")
