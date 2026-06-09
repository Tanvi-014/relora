"""add missing indexes and webhooks status check constraint

Revision ID: 20260607_0016
Revises: 20260607_0015
Create Date: 2026-06-07

"""
from alembic import op
import sqlalchemy as sa

revision = "20260607_0016"
down_revision = "20260607_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 5.1 — compound partial index for the CLAIM_QUERY ordering-key NOT EXISTS subquery.
    # Eliminates the full scan of all 'processing' webhooks under concurrent load.
    # Note: CONCURRENTLY cannot run inside a transaction (Alembic wraps each migration
    # in one). env.py sets lock_timeout='5s' so the build can't block indefinitely.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_webhooks_ordering_key_status
        ON webhooks (ordering_key, status)
        WHERE ordering_key IS NOT NULL
    """)

    # 5.2 — index on delivery_attempts.attempted_at used by destination_stats
    # range queries (e.g. WHERE attempted_at >= NOW() - INTERVAL '1 hour').
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_delivery_attempts_attempted_at
        ON delivery_attempts (attempted_at)
    """)

    # 5.3 — enforce valid status values at the DB level.
    # Two-step approach: NOT VALID takes only a brief ACCESS EXCLUSIVE lock to
    # register the constraint without scanning existing rows. VALIDATE then
    # acquires SHARE UPDATE EXCLUSIVE (allows concurrent DML) to check them.
    op.execute("""
        ALTER TABLE webhooks
        ADD CONSTRAINT chk_webhook_status
        CHECK (status IN ('pending', 'processing', 'completed', 'failed'))
        NOT VALID
    """)
    op.execute("ALTER TABLE webhooks VALIDATE CONSTRAINT chk_webhook_status")


def downgrade() -> None:
    op.execute("ALTER TABLE webhooks DROP CONSTRAINT IF EXISTS chk_webhook_status")
    op.execute("DROP INDEX IF EXISTS ix_delivery_attempts_attempted_at")
    op.execute("DROP INDEX IF EXISTS ix_webhooks_ordering_key_status")
