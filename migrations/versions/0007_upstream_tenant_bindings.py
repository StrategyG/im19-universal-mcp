"""Authoritative tenant binding for verified IM19 MCP subjects.

Revision ID: 0007_upstream_tenant_bindings
Revises: 0006_write_approvals
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_upstream_tenant_bindings"
down_revision = "0006_write_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_subject_tenant_bindings",
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_mcp_subject_tenant_bindings_status",
        ),
        sa.CheckConstraint(
            "length(trim(subject)) BETWEEN 1 AND 255",
            name="ck_mcp_subject_tenant_bindings_subject",
        ),
        sa.CheckConstraint(
            "length(trim(tenant_id)) BETWEEN 1 AND 128",
            name="ck_mcp_subject_tenant_bindings_tenant",
        ),
    )
    op.create_index(
        "ix_mcp_subject_tenant_bindings_tenant",
        "mcp_subject_tenant_bindings",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_subject_tenant_bindings_tenant",
        table_name="mcp_subject_tenant_bindings",
    )
    op.drop_table("mcp_subject_tenant_bindings")
