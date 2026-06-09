"""
Tests for worker recovery scenarios:
- Stuck 'processing' webhooks reset to 'pending' on startup
- Replay jobs stuck 'running' are failed on startup
- Consumer poll ack token correctly completes a previous batch
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# _recover_stuck_webhooks
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_recover_stuck_webhooks_resets_to_pending():
    """Webhooks stuck in 'processing' for > 10 min are reset to 'pending'."""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 3
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.async_session", return_value=ctx):
        from app.api_main import _recover_stuck_webhooks
        await _recover_stuck_webhooks()

    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()
    sql = str(mock_db.execute.call_args[0][0])
    assert "processing" in sql
    assert "pending" in sql


@pytest.mark.anyio
async def test_recover_stuck_webhooks_noop_when_none_stuck():
    """Recovery commits but logs nothing when rowcount is 0."""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.async_session", return_value=ctx):
        from app.api_main import _recover_stuck_webhooks
        await _recover_stuck_webhooks()

    mock_db.commit.assert_called_once()


@pytest.mark.anyio
async def test_recover_stuck_webhooks_sql_targets_10min_window():
    """The recovery SQL must filter on updated_at < NOW() - INTERVAL '10 minutes'."""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.async_session", return_value=ctx):
        from app.api_main import _recover_stuck_webhooks
        await _recover_stuck_webhooks()

    sql = str(mock_db.execute.call_args[0][0])
    assert "10 minutes" in sql


# ---------------------------------------------------------------------------
# _recover_stuck_replay_jobs
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_recover_stuck_replay_jobs_marks_failed():
    """Replay jobs stuck 'running' are marked 'failed' on startup."""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.async_session", return_value=ctx):
        from app.api_main import _recover_stuck_replay_jobs
        await _recover_stuck_replay_jobs()

    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()
    sql = str(mock_db.execute.call_args[0][0])
    assert "running" in sql
    assert "failed" in sql


# ---------------------------------------------------------------------------
# Consumer poll ack token
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_consumer_poll_ack_completes_previous_batch():
    """When ack_token is provided, previous batch is marked 'completed'."""
    mock_db = AsyncMock()

    ack_result = MagicMock()
    ack_result.rowcount = 5

    poll_result = MagicMock()
    poll_result.fetchall.return_value = []

    mock_db.execute = AsyncMock(side_effect=[ack_result, poll_result])
    mock_db.commit = AsyncMock()

    from app.routers.consumer import poll_events
    result = await poll_events(
        consumer_id="worker-1",
        limit=100,
        ack_token="prev-token-abc",
        tenant_id="tenant_a",
        db=mock_db,
    )

    assert mock_db.execute.call_count == 2
    ack_sql = str(mock_db.execute.call_args_list[0][0][0])
    assert "completed" in ack_sql
    assert "poll_ack_token" in ack_sql

    assert result["count"] == 0
    assert result["ack_token"] is None


@pytest.mark.anyio
async def test_consumer_poll_without_ack_skips_completion():
    """When no ack_token is provided, no completion UPDATE is issued."""
    mock_db = AsyncMock()

    poll_result = MagicMock()
    poll_result.fetchall.return_value = []
    mock_db.execute = AsyncMock(return_value=poll_result)
    mock_db.commit = AsyncMock()

    from app.routers.consumer import poll_events
    result = await poll_events(
        consumer_id="worker-1",
        limit=10,
        ack_token=None,
        tenant_id="tenant_a",
        db=mock_db,
    )

    # Only one execute call: the SELECT/UPDATE for polling
    assert mock_db.execute.call_count == 1
    assert result["count"] == 0


@pytest.mark.anyio
async def test_consumer_poll_returns_ack_token_only_when_events_present():
    """ack_token is None in the response when no events are returned."""
    mock_db = AsyncMock()
    poll_result = MagicMock()
    poll_result.fetchall.return_value = []
    mock_db.execute = AsyncMock(return_value=poll_result)
    mock_db.commit = AsyncMock()

    from app.routers.consumer import poll_events
    result = await poll_events(
        consumer_id="worker-1",
        limit=10,
        ack_token=None,
        tenant_id="tenant_a",
        db=mock_db,
    )
    assert result["ack_token"] is None


@pytest.mark.anyio
async def test_consumer_poll_ack_is_tenant_scoped():
    """The ack UPDATE filters by both poll_ack_token AND tenant_id to prevent cross-tenant completion."""
    mock_db = AsyncMock()

    ack_result = MagicMock()
    ack_result.rowcount = 0
    poll_result = MagicMock()
    poll_result.fetchall.return_value = []
    mock_db.execute = AsyncMock(side_effect=[ack_result, poll_result])
    mock_db.commit = AsyncMock()

    from app.routers.consumer import poll_events
    await poll_events(
        consumer_id="worker-1",
        limit=10,
        ack_token="some-token",
        tenant_id="tenant_a",
        db=mock_db,
    )

    ack_call = mock_db.execute.call_args_list[0]
    bound_params = ack_call[0][1] if len(ack_call[0]) > 1 else ack_call[1].get("parameters", {})
    # Parameters are passed as second positional arg in the execute call
    params = mock_db.execute.call_args_list[0][0][1]
    assert params["tid"] == "tenant_a"
    assert params["token"] == "some-token"
