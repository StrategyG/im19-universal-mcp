"""Durable, owner-isolated autonomous plans and step leases.

Revision ID: 0009_autonomous_workflow
Revises: 0008_arusia_artifacts
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_autonomous_workflow"
down_revision = "0008_arusia_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "im19_autonomous_runs",
        sa.Column("run_id", sa.String(32), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("subject", "plan_sha256",
                            name="uq_im19_owner_compiled_plan"),
    )
    op.create_index("ix_im19_autonomous_runs_subject", "im19_autonomous_runs", ["subject"])
    op.create_table(
        "im19_autonomous_steps",
        sa.Column("step_id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("im19_autonomous_runs.run_id"),
                  nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("operation", sa.String(48), nullable=False),
        sa.Column("depends_json", sa.Text(), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_hash", sa.String(64), nullable=False),
        sa.Column("lease_until", sa.Float(), nullable=False),
        sa.Column("retry_at", sa.Float(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("run_id", "key", name="uq_im19_autonomous_step_key"),
    )
    op.create_index("ix_im19_autonomous_steps_run_id", "im19_autonomous_steps", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_im19_autonomous_steps_run_id", table_name="im19_autonomous_steps")
    op.drop_table("im19_autonomous_steps")
    op.drop_index("ix_im19_autonomous_runs_subject", table_name="im19_autonomous_runs")
    op.drop_table("im19_autonomous_runs")
