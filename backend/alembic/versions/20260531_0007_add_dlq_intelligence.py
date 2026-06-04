"""Add DLQ Intelligence features: failure classification and incidents.

Revision ID: 20260531_0007
Revises: 20260529_0006
Create Date: 2026-05-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260531_0007"
down_revision: Union[str, None] = "20260529_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add failure classification columns to delivery_attempts
    op.add_column(
        "delivery_attempts",
        sa.Column("failure_category", sa.String(), nullable=True),
    )
    op.add_column(
        "delivery_attempts",
        sa.Column("failure_subcategory", sa.String(), nullable=True),
    )
    op.add_column(
        "delivery_attempts",
        sa.Column("failure_severity", sa.String(), nullable=True),
    )
    op.add_column(
        "delivery_attempts",
        sa.Column("failure_recoverability", sa.String(), nullable=True),
    )
    op.add_column(
        "delivery_attempts",
        sa.Column("error_signature", sa.String(), nullable=True),
    )
    
    # Create indexes for failure classification
    op.create_index(
        "ix_delivery_attempts_failure_category",
        "delivery_attempts",
        ["failure_category"],
    )
    op.create_index(
        "ix_delivery_attempts_error_signature",
        "delivery_attempts",
        ["error_signature"],
    )
    
    # Create incidents table
    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("incident_signature", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("failure_category", sa.String(), nullable=True),
        sa.Column("failure_subcategory", sa.String(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("affected_webhook_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trend_state", sa.String(), nullable=True),
        sa.Column("growth_rate_15m", sa.Integer(), nullable=False),
        sa.Column("growth_rate_1h", sa.Integer(), nullable=False),
        sa.Column("growth_rate_6h", sa.Integer(), nullable=False),
        sa.Column("growth_rate_24h", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("recoverability", sa.String(), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("expected_recovery_difficulty", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Create indexes for incidents
    op.create_index(
        "ix_incidents_project_id",
        "incidents",
        ["project_id"],
    )
    op.create_index(
        "ix_incidents_destination_id",
        "incidents",
        ["destination_id"],
    )
    op.create_index(
        "ix_incidents_state",
        "incidents",
        ["state"],
    )
    op.create_index(
        "ix_incidents_first_seen_at",
        "incidents",
        ["first_seen_at"],
    )
    op.create_index(
        "ix_incidents_incident_signature",
        "incidents",
        ["incident_signature"],
    )


def downgrade() -> None:
    # Drop incidents table
    op.drop_index("ix_incidents_incident_signature", table_name="incidents")
    op.drop_index("ix_incidents_first_seen_at", table_name="incidents")
    op.drop_index("ix_incidents_state", table_name="incidents")
    op.drop_index("ix_incidents_destination_id", table_name="incidents")
    op.drop_index("ix_incidents_project_id", table_name="incidents")
    op.drop_table("incidents")
    
    # Drop failure classification indexes
    op.drop_index("ix_delivery_attempts_error_signature", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_failure_category", table_name="delivery_attempts")
    
    # Drop failure classification columns
    op.drop_column("delivery_attempts", "error_signature")
    op.drop_column("delivery_attempts", "failure_recoverability")
    op.drop_column("delivery_attempts", "failure_severity")
    op.drop_column("delivery_attempts", "failure_subcategory")
    op.drop_column("delivery_attempts", "failure_category")
