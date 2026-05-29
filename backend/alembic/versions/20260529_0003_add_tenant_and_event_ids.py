"""Add tenant and event correlation ids.

Revision ID: 20260529_0003
Revises: 20260527_0002
Create Date: 2026-05-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260529_0003"
down_revision: Union[str, None] = "20260527_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("webhooks", sa.Column("tenant_id", sa.String(), nullable=True))
    op.execute("UPDATE webhooks SET tenant_id = 'anonymous' WHERE tenant_id IS NULL")
    op.alter_column("webhooks", "tenant_id", nullable=False, server_default="anonymous")
    op.add_column("webhooks", sa.Column("event_id", sa.String(), nullable=True))
    op.execute("UPDATE webhooks SET event_id = id::text WHERE event_id IS NULL")
    op.alter_column("webhooks", "event_id", nullable=False)
    op.create_index("ix_webhooks_tenant_created_at", "webhooks", ["tenant_id", "created_at"], unique=False)
    op.create_index("ix_webhooks_event_id", "webhooks", ["event_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_webhooks_event_id", table_name="webhooks")
    op.drop_index("ix_webhooks_tenant_created_at", table_name="webhooks")
    op.drop_column("webhooks", "event_id")
    op.drop_column("webhooks", "tenant_id")
