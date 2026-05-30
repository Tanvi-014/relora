"""v2 production upgrade: destinations, rate_limit_buckets, event_types, replay_jobs,
FIFO ordering_key, consumer polling fields, delivery attempt response_headers,
retry_strategy_used, is_simulation, GIN search index.

Revision ID: 20260529_0006
Revises: 20260529_0005
Create Date: 2026-05-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260529_0006"
down_revision: Union[str, None] = "20260529_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # destinations table
    # ------------------------------------------------------------------ #
    op.create_table(
        "destinations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("backoff_base_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("ordering_key_field", sa.String(255), nullable=True),
        sa.Column("transform_type", sa.String(20), nullable=False, server_default="none"),
        sa.Column("transform_code", sa.Text(), nullable=True),
        sa.Column("transform_map", postgresql.JSONB(), nullable=True),
        sa.Column("filter_expression", sa.Text(), nullable=True),
        sa.Column("webhook_secret", sa.Text(), nullable=True),
        sa.Column("custom_headers", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("circuit_state", sa.String(20), nullable=False, server_default="closed"),
        sa.Column("circuit_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("circuit_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("circuit_next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_destinations_project_name"),
    )
    op.create_index("ix_destinations_project_id", "destinations", ["project_id"])

    # ------------------------------------------------------------------ #
    # rate_limit_buckets table (Postgres token bucket)
    # ------------------------------------------------------------------ #
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False, server_default="60"),
        sa.Column("last_refill", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_tokens", sa.Float(), nullable=False, server_default="60"),
        sa.Column("refill_rate", sa.Float(), nullable=False, server_default="1.0"),
        sa.PrimaryKeyConstraint("key"),
    )

    # ------------------------------------------------------------------ #
    # event_types table
    # ------------------------------------------------------------------ #
    op.create_table(
        "event_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("schema", postgresql.JSONB(), nullable=True),
        sa.Column("example_payload", postgresql.JSONB(), nullable=True),
        sa.Column("version", sa.String(50), nullable=False, server_default="1"),
        sa.Column("deprecated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_event_types_project_name_version"),
    )
    op.create_index("ix_event_types_project_id", "event_types", ["project_id"])

    # ------------------------------------------------------------------ #
    # replay_jobs table
    # ------------------------------------------------------------------ #
    op.create_table(
        "replay_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("to_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("replay_rate_per_minute", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------------ #
    # webhooks table — new columns
    # ------------------------------------------------------------------ #
    op.add_column("webhooks", sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("webhooks", sa.Column("ordering_key", sa.String(255), nullable=True))
    op.add_column("webhooks", sa.Column("consumer_id", sa.String(255), nullable=True))
    op.add_column("webhooks", sa.Column("poll_ack_token", sa.String(255), nullable=True))
    op.add_column("webhooks", sa.Column("is_simulation", sa.Boolean(), nullable=False, server_default="false"))

    op.create_foreign_key(
        "fk_webhooks_destination_id",
        "webhooks", "destinations",
        ["destination_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_webhooks_ordering_key", "webhooks", ["ordering_key"])
    op.create_index("ix_webhooks_consumer_id", "webhooks", ["consumer_id"])

    # GIN index for full-text payload search
    op.execute(
        "CREATE INDEX ix_webhooks_payload_gin ON webhooks USING GIN (payload jsonb_path_ops)"
    )

    # ------------------------------------------------------------------ #
    # delivery_attempts table — new columns
    # ------------------------------------------------------------------ #
    op.add_column("delivery_attempts", sa.Column("response_headers", postgresql.JSONB(), nullable=True))
    op.add_column("delivery_attempts", sa.Column("retry_strategy_used", sa.String(50), nullable=True))

    # ------------------------------------------------------------------ #
    # alert_configs table — create it here (was previously only created by
    # AUTO_CREATE_TABLES; now properly tracked in migrations)
    # ------------------------------------------------------------------ #
    op.create_table(
        "alert_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, server_default="anonymous"),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("channel_type", sa.String(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_configs_tenant_id", "alert_configs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_configs_tenant_id", "alert_configs")
    op.drop_table("alert_configs")
    op.drop_column("delivery_attempts", "retry_strategy_used")
    op.drop_column("delivery_attempts", "response_headers")
    op.execute("DROP INDEX IF EXISTS ix_webhooks_payload_gin")
    op.drop_index("ix_webhooks_consumer_id", "webhooks")
    op.drop_index("ix_webhooks_ordering_key", "webhooks")
    op.drop_constraint("fk_webhooks_destination_id", "webhooks", type_="foreignkey")
    op.drop_column("webhooks", "is_simulation")
    op.drop_column("webhooks", "poll_ack_token")
    op.drop_column("webhooks", "consumer_id")
    op.drop_column("webhooks", "ordering_key")
    op.drop_column("webhooks", "destination_id")
    op.drop_table("replay_jobs")
    op.drop_index("ix_event_types_project_id", "event_types")
    op.drop_table("event_types")
    op.drop_table("rate_limit_buckets")
    op.drop_index("ix_destinations_project_id", "destinations")
    op.drop_table("destinations")
