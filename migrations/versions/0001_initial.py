"""Initial encrypted token vault and audit metadata schema.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_table("oauth_credentials",
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column("service", sa.String(64), primary_key=True),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_table("audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.String(40), nullable=False),
        sa.Column("subject_pseudonym", sa.String(64), nullable=False),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("oauth_credentials")
    op.drop_table("oauth_states")
