"""add alert thresholds and member role update support

Revision ID: 20260607_0015
Revises: 20260606_0014
Create Date: 2026-06-07

"""
from alembic import op
import sqlalchemy as sa

revision = "20260607_0015"
down_revision = "20260606_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alert threshold columns — both nullable so existing rows are unaffected
    op.add_column("alert_configs", sa.Column("dlq_threshold", sa.Integer(), nullable=True))
    op.add_column("alert_configs", sa.Column("error_rate_threshold", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("alert_configs", "error_rate_threshold")
    op.drop_column("alert_configs", "dlq_threshold")
