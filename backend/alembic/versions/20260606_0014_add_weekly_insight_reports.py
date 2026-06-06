"""add weekly_insight_reports table

Revision ID: 20260606_0014
Revises: 20260606_0013
Create Date: 2026-06-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260606_0014"
down_revision = "20260606_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_insight_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("week_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("week_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("grade", sa.String(4), nullable=False),
        sa.Column("reliability_score", sa.Float(), nullable=False),
        sa.Column("score_delta", sa.Float(), nullable=True),
        sa.Column("report_data", JSONB(), nullable=False, server_default="{}"),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "week_start", name="uq_weekly_report_tenant_week"),
    )
    op.create_index("ix_weekly_insight_reports_tenant_id", "weekly_insight_reports", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_weekly_insight_reports_tenant_id", "weekly_insight_reports")
    op.drop_table("weekly_insight_reports")
