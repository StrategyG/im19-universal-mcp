"""ARUSIA v9 shot-level, owner-isolated deterministic workflow.

Revision ID: 0007_arusia_workflow
Revises: 0006_write_approvals
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_arusia_workflow"
down_revision = "0006_write_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "arusia_episodes",
        sa.Column("episode_id", sa.String(36), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("subject", "code", name="uq_arusia_owner_episode_code"),
    )
    op.create_index("ix_arusia_episodes_subject", "arusia_episodes", ["subject"])
    op.create_table(
        "arusia_tasks",
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), sa.ForeignKey("arusia_episodes.episode_id"), nullable=False),
        sa.Column("shot_id", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_digest", sa.String(64), nullable=False),
        sa.Column("lease_until", sa.Float(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("episode_id", "shot_id", "position", name="uq_arusia_shot_stage"),
    )
    op.create_index("ix_arusia_tasks_episode_id", "arusia_tasks", ["episode_id"])


def downgrade() -> None:
    op.drop_index("ix_arusia_tasks_episode_id", table_name="arusia_tasks")
    op.drop_table("arusia_tasks")
    op.drop_index("ix_arusia_episodes_subject", table_name="arusia_episodes")
    op.drop_table("arusia_episodes")
