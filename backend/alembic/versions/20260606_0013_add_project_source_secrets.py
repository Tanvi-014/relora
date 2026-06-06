"""add project source_secrets

Revision ID: 20260606_0013
Revises: 20260605_0012
Create Date: 2026-06-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260606_0013"
down_revision = "20260605_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("source_secrets", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("projects", "source_secrets")
