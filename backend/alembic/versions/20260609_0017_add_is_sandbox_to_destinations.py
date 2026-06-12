"""add is_sandbox to destinations

Revision ID: 20260609_0017
Revises: 20260607_0016
Create Date: 2026-06-09

"""
from alembic import op
import sqlalchemy as sa

revision = "20260609_0017"
down_revision = "20260607_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "destinations",
        sa.Column("is_sandbox", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_destinations_is_sandbox", "destinations", ["is_sandbox"])


def downgrade() -> None:
    op.drop_index("ix_destinations_is_sandbox", table_name="destinations")
    op.drop_column("destinations", "is_sandbox")
