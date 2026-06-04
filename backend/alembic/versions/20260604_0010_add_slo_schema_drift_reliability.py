"""Add SLO config, schema fingerprints, schema changes, reliability snapshots.

Revision ID: 20260604_0010
Revises: 20260604_0009
Create Date: 2026-06-04
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260604_0010"
down_revision: Union[str, None] = "20260604_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── SLO config columns on destinations ──────────────────────────────────
    op.add_column("destinations", sa.Column("slo_target_pct", sa.Float(), nullable=True))
    op.add_column("destinations", sa.Column("slo_window_minutes", sa.Integer(), nullable=False, server_default="60"))

    # ── Schema fingerprints — one row per (tenant_id, source_key) ───────────
    op.create_table(
        "schema_fingerprints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),   # e.g. "stripe", "github", or event_type name
        sa.Column("fingerprint", sa.String(64), nullable=False),   # SHA-256 hex of sorted key structure
        sa.Column("key_structure", JSONB, nullable=False),         # sorted list of dotted key paths
        sa.Column("sample_payload", JSONB, nullable=True),         # redacted sample for diff display
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_schema_fingerprints_tenant_source", "schema_fingerprints", ["tenant_id", "source_key"], unique=True)

    # ── Schema changes — written when fingerprint changes ───────────────────
    op.create_table(
        "schema_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("old_fingerprint", sa.String(64), nullable=True),
        sa.Column("new_fingerprint", sa.String(64), nullable=False),
        sa.Column("added_keys", JSONB, nullable=True),    # keys in new but not old
        sa.Column("removed_keys", JSONB, nullable=True),  # keys in old but not new
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_schema_changes_tenant_detected", "schema_changes", ["tenant_id", "detected_at"])
    op.create_index("ix_schema_changes_tenant_unacked", "schema_changes", ["tenant_id", "acknowledged_at"])

    # ── Destination reliability snapshots — one row per (destination, date) ─
    op.create_table(
        "destination_reliability_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("destination_id", UUID(as_uuid=True), sa.ForeignKey("destinations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_deliveries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_deliveries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_deliveries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_latency_ms", sa.Float(), nullable=True),
        sa.Column("p95_latency_ms", sa.Float(), nullable=True),
        sa.Column("success_rate", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_reliability_snapshots_dest_date", "destination_reliability_snapshots", ["destination_id", "date"], unique=True)


def downgrade() -> None:
    op.drop_table("destination_reliability_snapshots")
    op.drop_table("schema_changes")
    op.drop_table("schema_fingerprints")
    op.drop_column("destinations", "slo_window_minutes")
    op.drop_column("destinations", "slo_target_pct")
