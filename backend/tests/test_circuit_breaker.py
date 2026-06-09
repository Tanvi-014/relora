"""Unit tests for circuit breaker state machine."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


@pytest.fixture
def mock_destination():
    dest = MagicMock()
    dest.id = uuid4()
    dest.circuit_state = "closed"
    dest.circuit_failure_count = 0
    dest.circuit_opened_at = None
    dest.circuit_next_retry_at = None
    dest.updated_at = None
    return dest


@pytest.fixture
def mock_db(mock_destination):
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_destination
    db.execute = AsyncMock(return_value=mock_result)
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_closed_circuit_allows_delivery(mock_db, mock_destination):
    from app.circuit_breaker import should_deliver
    mock_destination.circuit_state = "closed"
    result = await should_deliver(mock_db, mock_destination.id)
    assert result is True


@pytest.mark.asyncio
async def test_open_circuit_blocks_delivery(mock_db, mock_destination):
    from app.circuit_breaker import should_deliver
    mock_destination.circuit_state = "open"
    # Set next retry far in the future
    mock_destination.circuit_next_retry_at = datetime.now(timezone.utc) + timedelta(hours=1)
    result = await should_deliver(mock_db, mock_destination.id)
    assert result is False


@pytest.mark.asyncio
async def test_open_circuit_transitions_to_half_open_after_timeout(mock_db, mock_destination):
    from app.circuit_breaker import should_deliver
    mock_destination.circuit_state = "open"
    # Cooldown has elapsed
    mock_destination.circuit_next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    result = await should_deliver(mock_db, mock_destination.id)
    assert result is True


@pytest.mark.asyncio
async def test_half_open_allows_probe(mock_db, mock_destination):
    from app.circuit_breaker import should_deliver
    mock_destination.circuit_state = "half_open"
    result = await should_deliver(mock_db, mock_destination.id)
    assert result is True


@pytest.mark.asyncio
async def test_success_in_closed_resets_failure_count(mock_db, mock_destination):
    from app.circuit_breaker import record_outcome
    mock_destination.circuit_state = "closed"
    mock_destination.circuit_failure_count = 3
    await record_outcome(mock_db, mock_destination.id, success=True)
    assert mock_destination.circuit_failure_count == 0


@pytest.mark.asyncio
async def test_failures_increment_count(mock_db, mock_destination):
    from app.circuit_breaker import record_outcome
    mock_destination.circuit_state = "closed"
    mock_destination.circuit_failure_count = 0
    await record_outcome(mock_db, mock_destination.id, success=False)
    assert mock_destination.circuit_failure_count == 1


@pytest.mark.asyncio
async def test_threshold_failures_open_circuit(mock_db, mock_destination):
    from app.circuit_breaker import record_outcome, FAILURE_THRESHOLD
    mock_destination.circuit_state = "closed"
    mock_destination.circuit_failure_count = FAILURE_THRESHOLD - 1
    await record_outcome(mock_db, mock_destination.id, success=False)
    assert mock_destination.circuit_state == "open"
    assert mock_destination.circuit_next_retry_at is not None


@pytest.mark.asyncio
async def test_success_in_half_open_closes_circuit(mock_db, mock_destination):
    from app.circuit_breaker import record_outcome
    mock_destination.circuit_state = "half_open"
    mock_destination.circuit_failure_count = 0
    await record_outcome(mock_db, mock_destination.id, success=True)
    assert mock_destination.circuit_state == "closed"


@pytest.mark.asyncio
async def test_half_open_single_success_insufficient(mock_db, mock_destination):
    """One success when failure_count == SUCCESS_TO_CLOSE must NOT close the circuit."""
    from app.circuit_breaker import record_outcome, SUCCESS_TO_CLOSE
    mock_destination.circuit_state = "half_open"
    # Simulate what should_deliver sets after open → half_open transition
    mock_destination.circuit_failure_count = SUCCESS_TO_CLOSE
    await record_outcome(mock_db, mock_destination.id, success=True)
    assert mock_destination.circuit_state == "half_open"
    assert mock_destination.circuit_failure_count == SUCCESS_TO_CLOSE - 1


@pytest.mark.asyncio
async def test_half_open_closes_after_exactly_success_to_close_successes(mock_db, mock_destination):
    """Exactly SUCCESS_TO_CLOSE consecutive successes must close the circuit."""
    from app.circuit_breaker import record_outcome, SUCCESS_TO_CLOSE
    mock_destination.circuit_state = "half_open"
    mock_destination.circuit_failure_count = SUCCESS_TO_CLOSE
    for i in range(SUCCESS_TO_CLOSE - 1):
        await record_outcome(mock_db, mock_destination.id, success=True)
        assert mock_destination.circuit_state == "half_open", f"should still be half_open after {i+1} success(es)"
    # Final success should close
    await record_outcome(mock_db, mock_destination.id, success=True)
    assert mock_destination.circuit_state == "closed"
    assert mock_destination.circuit_failure_count == 0


@pytest.mark.asyncio
async def test_failure_in_half_open_opens_circuit(mock_db, mock_destination):
    """A failure while half_open should push the failure count back up and re-open if threshold met."""
    from app.circuit_breaker import record_outcome, FAILURE_THRESHOLD
    mock_destination.circuit_state = "half_open"
    mock_destination.circuit_failure_count = FAILURE_THRESHOLD - 1
    await record_outcome(mock_db, mock_destination.id, success=False)
    assert mock_destination.circuit_state == "open"


@pytest.mark.asyncio
async def test_circuit_remains_closed_on_sustained_success(mock_db, mock_destination):
    """Many successes in closed state keep failure count at 0."""
    from app.circuit_breaker import record_outcome
    mock_destination.circuit_state = "closed"
    mock_destination.circuit_failure_count = 5
    for _ in range(3):
        await record_outcome(mock_db, mock_destination.id, success=True)
    assert mock_destination.circuit_state == "closed"
    assert mock_destination.circuit_failure_count == 0
