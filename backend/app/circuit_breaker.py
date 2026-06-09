"""
Per-destination circuit breaker backed by the destinations table.
States: closed (normal) → open (blocking) → half_open (probing) → closed
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("relora.circuit_breaker")

FAILURE_THRESHOLD = 10       # consecutive failures to trip open
HALF_OPEN_TIMEOUT = 5        # minutes to wait before probing
SUCCESS_TO_CLOSE = 2         # successes in half_open to re-close


class CircuitExpireWrapper:
    """Wrapper to ensure circuit states reset properly on timeout."""

    def __init__(self, destination_id: UUID):
        self.destination_id = destination_id
        self.last_check_time = None

    async def check_expiry(self, db: AsyncSession) -> bool:
        """Check if circuit should be expired/reset. Returns True if expired."""
        from app.models import Destination
        result = await db.execute(
            select(Destination).where(Destination.id == self.destination_id)
        )
        dest = result.scalar_one_or_none()

        if not dest:
            return True

        now = datetime.now(timezone.utc)

        # If circuit has been open for more than 30 minutes, force reset
        if dest.circuit_state == "open" and dest.circuit_opened_at:
            if (now - dest.circuit_opened_at) > timedelta(minutes=30):
                logger.info("Circuit timeout forcing reset for destination %s", self.destination_id)
                await _set_state(db, self.destination_id, "closed")
                return True

        self.last_check_time = now
        return False


async def should_deliver(db: AsyncSession, destination_id: UUID) -> bool:
    """Returns False if circuit is OPEN and cooldown not elapsed."""
    from app.models import Destination
    result = await db.execute(select(Destination).where(Destination.id == destination_id))
    dest = result.scalar_one_or_none()
    if not dest:
        return True

    state = dest.circuit_state
    if state == "closed":
        return True
    if state == "open":
        if dest.circuit_next_retry_at and datetime.now(timezone.utc) >= dest.circuit_next_retry_at:
            # CAS: atomically transition open → half_open so that only one writer
            # wins; any worker that reads 'open' with an elapsed timer can deliver
            # (the circuit is transitioning regardless of who wins the write).
            await db.execute(
                text("""
                UPDATE destinations SET circuit_state = 'half_open',
                  circuit_failure_count = :stc, updated_at = NOW()
                WHERE id = :id
                  AND circuit_state = 'open'
                  AND circuit_next_retry_at <= NOW()
                """),
                {"id": destination_id, "stc": SUCCESS_TO_CLOSE},
            )
            await db.commit()
            return True
        return False
    # half_open: let probe requests through
    return True


async def record_outcome(db: AsyncSession, destination_id: UUID, success: bool) -> None:
    from app.models import Destination
    result = await db.execute(
        select(Destination).where(Destination.id == destination_id).with_for_update()
    )
    dest = result.scalar_one_or_none()
    if not dest:
        return

    now = datetime.now(timezone.utc)

    if success:
        if dest.circuit_state == "half_open":
            dest.circuit_failure_count = max(0, dest.circuit_failure_count - 1)
            if dest.circuit_failure_count <= 0:
                dest.circuit_state = "closed"
                dest.circuit_failure_count = 0
                logger.info("Circuit closed for destination %s", destination_id)
        elif dest.circuit_state == "closed":
            dest.circuit_failure_count = 0
    else:
        dest.circuit_failure_count += 1
        if dest.circuit_failure_count >= FAILURE_THRESHOLD and dest.circuit_state != "open":
            dest.circuit_state = "open"
            dest.circuit_opened_at = now
            dest.circuit_next_retry_at = now + timedelta(minutes=HALF_OPEN_TIMEOUT)
            logger.warning(
                "Circuit OPENED for destination %s after %d failures",
                destination_id, dest.circuit_failure_count,
            )
            try:
                from app.telemetry import record_circuit_trip
                record_circuit_trip(str(destination_id))
            except Exception:
                pass

    dest.updated_at = now
    await db.commit()


async def _set_state(db: AsyncSession, destination_id: UUID, state: str) -> None:
    from app.models import Destination
    result = await db.execute(
        select(Destination).where(Destination.id == destination_id).with_for_update()
    )
    dest = result.scalar_one_or_none()
    if dest:
        dest.circuit_state = state
        dest.updated_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info("Circuit state → %s for destination %s", state, destination_id)
