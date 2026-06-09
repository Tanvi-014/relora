"""
Replay edge-case tests:
- max_retries=0: a webhook with max_retries=0 should still be re-queued with
  the destination's (or default) max_retries on single replay, not instantly DLQ'd.
- Bulk replay with only_failed=True (default) does NOT re-queue completed events.
- Cross-status bulk replay with force=True (future guard: completed events are
  replayed only when explicitly requested).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Single-webhook replay — max_retries=0 edge case
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_replay_single_resets_max_retries_from_destination():
    """
    A webhook that originally had max_retries=0 (e.g. a test event) must be
    re-queued with the destination's configured max_retries, not 0.
    This prevents the replayed webhook from immediately DLQ-ing.
    """
    from app.routers.webhooks import replay_webhook
    from unittest.mock import Mock
    from fastapi import Request

    webhook_id = uuid4()
    dest_id = uuid4()

    mock_webhook = MagicMock()
    mock_webhook.id = webhook_id
    mock_webhook.tenant_id = "tenant_a"
    mock_webhook.destination_id = dest_id
    mock_webhook.status = "failed"
    mock_webhook.max_retries = 0   # was a test event

    mock_dest = MagicMock()
    mock_dest.max_retries = 5   # destination configured for 5 retries

    mock_db = AsyncMock()

    wh_result = MagicMock()
    wh_result.scalar_one_or_none.return_value = mock_webhook

    dest_result = MagicMock()
    dest_result.scalar_one_or_none.return_value = mock_dest

    mock_db.execute = AsyncMock(side_effect=[wh_result, dest_result])
    mock_db.commit = AsyncMock()

    request = Mock(spec=Request)
    request.headers = {}
    request.client = None

    with patch("app.audit.audit", new_callable=AsyncMock):
        result = await replay_webhook(
            request=request,
            webhook_id=webhook_id,
            tenant_id="tenant_a",
            db=mock_db,
        )

    assert result["success"] is True
    # The webhook's max_retries must have been updated away from 0
    assert mock_webhook.max_retries != 0
    assert mock_webhook.max_retries == 5


@pytest.mark.anyio
async def test_replay_single_uses_default_max_retries_when_no_destination():
    """
    A webhook without a destination_id uses DEFAULT_MAX_RETRIES on replay.
    """
    from app.routers.webhooks import replay_webhook
    from unittest.mock import Mock
    from fastapi import Request

    webhook_id = uuid4()

    mock_webhook = MagicMock()
    mock_webhook.id = webhook_id
    mock_webhook.tenant_id = "tenant_a"
    mock_webhook.destination_id = None
    mock_webhook.status = "failed"
    mock_webhook.max_retries = 0

    mock_db = AsyncMock()
    wh_result = MagicMock()
    wh_result.scalar_one_or_none.return_value = mock_webhook
    mock_db.execute = AsyncMock(return_value=wh_result)
    mock_db.commit = AsyncMock()

    request = Mock(spec=Request)
    request.headers = {}
    request.client = None

    with patch("app.audit.audit", new_callable=AsyncMock), \
         patch("app.config.settings") as mock_settings:
        mock_settings.DEFAULT_MAX_RETRIES = 5
        result = await replay_webhook(
            request=request,
            webhook_id=webhook_id,
            tenant_id="tenant_a",
            db=mock_db,
        )

    assert result["success"] is True
    assert mock_webhook.max_retries == 5


@pytest.mark.anyio
async def test_replay_single_returns_404_for_unknown_webhook():
    """Replaying a non-existent webhook returns 404."""
    from app.routers.webhooks import replay_webhook
    from unittest.mock import Mock
    from fastapi import Request

    mock_db = AsyncMock()
    not_found = MagicMock()
    not_found.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=not_found)

    request = Mock(spec=Request)
    request.headers = {}
    request.client = None

    with pytest.raises(HTTPException) as exc:
        await replay_webhook(
            request=request,
            webhook_id=uuid4(),
            tenant_id="tenant_a",
            db=mock_db,
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Bulk replay — only_failed default prevents re-delivery of completed events
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_bulk_replay_only_failed_is_default():
    """
    replay_time_window defaults to only_failed=True, meaning the generated SQL
    includes AND status = 'failed'.  This prevents re-delivering events that
    already completed successfully.  We verify by checking the count SQL text.
    """
    from app.routers.webhooks import replay_time_window

    project = MagicMock()
    project.id = uuid4()

    mock_db = AsyncMock()

    project_result = MagicMock()
    project_result.scalar_one_or_none.return_value = project

    count_result = MagicMock()
    count_result.scalar.return_value = 0   # no events in window

    replay_job = MagicMock()
    replay_job.id = uuid4()
    replay_job.total_count = 0

    mock_db.execute = AsyncMock(side_effect=[project_result, count_result])
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    now = datetime.now(timezone.utc)
    body = {
        "from_time": (now - timedelta(hours=1)).isoformat(),
        "to_time": now.isoformat(),
        # only_failed NOT specified — should default to True
    }

    with patch("app.routers.webhooks.ReplayJob", return_value=replay_job), \
         patch("app.routers.webhooks._fire_and_forget"):
        result = await replay_time_window(body=body, tenant_id="hk_live_key", db=mock_db)

    assert result["total_count"] == 0
    # SQL for count must include status = 'failed'
    count_sql = str(mock_db.execute.call_args_list[1][0][0])
    assert "failed" in count_sql


@pytest.mark.anyio
async def test_bulk_replay_does_not_accept_over_safety_cap():
    """
    replay_time_window enforces _MAX_REPLAY_BATCH and requires force=True to
    override when the count exceeds the cap.
    """
    from app.routers.webhooks import replay_time_window, _MAX_REPLAY_BATCH

    project = MagicMock()
    project.id = uuid4()

    mock_db = AsyncMock()

    project_result = MagicMock()
    project_result.scalar_one_or_none.return_value = project

    count_result = MagicMock()
    count_result.scalar.return_value = _MAX_REPLAY_BATCH + 1

    mock_db.execute = AsyncMock(side_effect=[project_result, count_result])
    mock_db.commit = AsyncMock()

    now = datetime.now(timezone.utc)
    body = {
        "from_time": (now - timedelta(days=30)).isoformat(),
        "to_time": now.isoformat(),
        "force": False,
    }

    with pytest.raises(HTTPException) as exc:
        await replay_time_window(body=body, tenant_id="hk_live_key", db=mock_db)
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_bulk_replay_all_statuses_with_force_true():
    """
    With only_failed=False and force=True, the count SQL should NOT include
    the status = 'failed' clause, allowing all statuses to be counted.
    """
    from app.routers.webhooks import replay_time_window

    project = MagicMock()
    project.id = uuid4()

    mock_db = AsyncMock()

    project_result = MagicMock()
    project_result.scalar_one_or_none.return_value = project

    count_result = MagicMock()
    count_result.scalar.return_value = 5

    replay_job = MagicMock()
    replay_job.id = uuid4()
    replay_job.total_count = 5

    mock_db.execute = AsyncMock(side_effect=[project_result, count_result])
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    with patch("app.routers.webhooks.ReplayJob", return_value=replay_job), \
         patch("app.routers.webhooks._fire_and_forget"):
        now = datetime.now(timezone.utc)
        body = {
            "from_time": (now - timedelta(hours=1)).isoformat(),
            "to_time": now.isoformat(),
            "only_failed": False,
            "force": True,
        }
        result = await replay_time_window(body=body, tenant_id="hk_live_key", db=mock_db)

    # Count SQL must NOT filter by status='failed' when only_failed=False
    count_call_sql = str(mock_db.execute.call_args_list[1][0][0])
    assert "status = 'failed'" not in count_call_sql
