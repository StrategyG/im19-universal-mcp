"""ARUSIA byte-verified asset and generated-artifact catalog.

Revision ID: 0008_arusia_artifacts
Revises: 0007_arusia_workflow
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_arusia_artifacts"
down_revision = "0007_arusia_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "arusia_artifacts",
        sa.Column("record_id", sa.String(32), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("subject", "asset_id", "sha256",
                            name="uq_arusia_artifact_version"),
    )
    op.create_index("ix_arusia_artifacts_subject", "arusia_artifacts", ["subject"])


def downgrade() -> None:
    op.drop_index("ix_arusia_artifacts_subject", table_name="arusia_artifacts")
    op.drop_table("arusia_artifacts")
