"""Add response_body_compressed flag to delivery_attempts.

Revision ID: 20260603_0008
Revises: 20260531_0007
Create Date: 2026-06-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260603_0008"
down_revision: Union[str, None] = "20260531_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "delivery_attempts",
        sa.Column(
            "response_body_compressed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("delivery_attempts", "response_body_compressed")
