"""Scope idempotency keys by tenant and destination.

Revision ID: 20260529_0004
Revises: 20260529_0003
Create Date: 2026-05-29
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260529_0004"
down_revision: Union[str, None] = "20260529_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_webhooks_destination_idempotency_key", table_name="webhooks")
    op.create_index(
        "ix_webhooks_tenant_destination_idempotency_key",
        "webhooks",
        ["tenant_id", "destination_url", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_webhooks_tenant_destination_idempotency_key", table_name="webhooks")
    op.create_index(
        "ix_webhooks_destination_idempotency_key",
        "webhooks",
        ["destination_url", "idempotency_key"],
        unique=True,
    )
