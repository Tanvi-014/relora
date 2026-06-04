"""Add audit_log table for tamper-evident change tracking.

Revision ID: 20260604_0009
Revises: 20260603_0008
Create Date: 2026-06-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260604_0009"
down_revision: Union[str, None] = "20260603_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),        # CREATE UPDATE DELETE REPLAY
        sa.Column("resource_type", sa.String(64), nullable=False), # destination webhook alert_config project
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("changes", JSONB, nullable=True),                # {before: {...}, after: {...}}
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_tenant_created", table_name="audit_log")
    op.drop_table("audit_log")
