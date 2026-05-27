"""Add idempotency key support.

Revision ID: 20260527_0002
Revises: 20260527_0001
Create Date: 2026-05-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260527_0002"
down_revision: Union[str, None] = "20260527_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("webhooks", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.create_index(
        "ix_webhooks_destination_idempotency_key",
        "webhooks",
        ["destination_url", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_webhooks_destination_idempotency_key", table_name="webhooks")
    op.drop_column("webhooks", "idempotency_key")
