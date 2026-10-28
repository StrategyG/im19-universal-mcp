"""Durable, owner-bound read-only browser inspection jobs.

Revision ID: 0007_browser_inspection_jobs
Revises: 0006_write_approvals
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_browser_inspection_jobs"
down_revision = "0006_write_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_inspection_jobs",
        sa.Column("job_id", sa.String(32), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("allowed_origin", sa.String(253), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_hash", sa.String(64), nullable=False),
        sa.Column("lease_expires_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("page_title", sa.String(200), nullable=False),
        sa.Column("blocked_requests", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(48), nullable=False),
        sa.UniqueConstraint("subject", "request_key", name="uq_browser_owner_request"),
    )
    op.create_index("ix_browser_inspection_jobs_subject", "browser_inspection_jobs", ["subject"])


def downgrade() -> None:
    op.drop_index("ix_browser_inspection_jobs_subject", table_name="browser_inspection_jobs")
    op.drop_table("browser_inspection_jobs")
