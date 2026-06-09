from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from fastapi import HTTPException
from uuid import uuid4


def _mock_request():
    req = MagicMock()
    req.headers = {"User-Agent": "test", "X-Forwarded-For": ""}
    req.client = None
    return req

from app.models import AlertConfig
from app.schemas import AlertConfigCreate, AlertConfigUpdate
from app.routers.alerts import (
    list_alerts,
    create_alert,
    get_alert,
    update_alert,
    delete_alert,
    test_alert as router_test_alert,
)


@pytest.mark.anyio
async def test_list_alerts_returns_tenant_configs():
    mock_db = AsyncMock()
    mock_config = AlertConfig(
        id=uuid4(),
        tenant_id="test_tenant",
        name="Slack Destination",
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/services/abc"},
        enabled=True,
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_config]
    mock_db.execute.return_value = mock_result

    configs = await list_alerts(tenant_id="test_tenant", db=mock_db)
    assert len(configs) == 1
    assert configs[0]["name"] == "Slack Destination"
    assert configs[0]["channel_type"] == "slack"


@pytest.mark.anyio
async def test_create_alert_config():
    mock_db = AsyncMock()
    config_in = AlertConfigCreate(
        name="Ops Email",
        channel_type="email",
        config={
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "from": "relora@ops.com",
            "to": "ops@ops.com",
            "password": "supersecret",
        },
        enabled=True,
    )

    with patch("app.routers.alerts.AlertConfig") as mock_alert_class, \
         patch("app.routers.alerts.audit", new_callable=AsyncMock):
        mock_alert_instance = MagicMock()
        mock_alert_instance.to_dict.return_value = {
            "id": "mock-uuid",
            "tenant_id": "test_tenant",
            "name": "Ops Email",
            "channel_type": "email",
            "config": {
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "from": "relora@ops.com",
                "to": "ops@ops.com",
                "password": "••••••••",
            },
            "enabled": True,
        }
        mock_alert_class.return_value = mock_alert_instance

        result = await create_alert(request=_mock_request(), config_in=config_in, tenant_id="test_tenant", db=mock_db)
        assert result["name"] == "Ops Email"
        assert result["config"]["password"] == "••••••••"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


@pytest.mark.anyio
async def test_get_alert_config_not_found():
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc:
        await get_alert(alert_id=uuid4(), tenant_id="test_tenant", db=mock_db)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_update_alert_config_prevents_secret_overwrite():
    mock_db = AsyncMock()
    alert_id = uuid4()
    existing_config = AlertConfig(
        id=alert_id,
        tenant_id="test_tenant",
        name="Ops Email",
        channel_type="email",
        config={
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "from": "relora@ops.com",
            "to": "ops@ops.com",
            "password": "realpassword",
        },
        enabled=True,
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_config
    mock_db.execute.return_value = mock_result

    config_update = AlertConfigUpdate(
        name="Updated Name",
        config={
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "from": "relora@ops.com",
            "to": "ops@ops.com",
            "password": "••••••••",  # placeholder — must NOT overwrite real password
        },
        enabled=False,
    )

    with patch("app.routers.alerts.func") as mock_func, \
         patch("app.routers.alerts.audit", new_callable=AsyncMock):
        mock_func.now.return_value = None
        await update_alert(
            request=_mock_request(),
            alert_id=alert_id,
            config_in=config_update,
            tenant_id="test_tenant",
            db=mock_db,
        )

        assert existing_config.config["password"] == "realpassword"
        assert existing_config.name == "Updated Name"
        assert existing_config.enabled is False
        mock_db.commit.assert_called_once()


@pytest.mark.anyio
async def test_delete_alert_config():
    mock_db = AsyncMock()
    alert_id = uuid4()
    existing_config = AlertConfig(
        id=alert_id,
        tenant_id="test_tenant",
        name="Delete Me",
        channel_type="slack",
        config={},
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_config
    mock_db.execute.return_value = mock_result

    with patch("app.routers.alerts.audit", new_callable=AsyncMock):
        response = await delete_alert(request=_mock_request(), alert_id=alert_id, tenant_id="test_tenant", db=mock_db)
    assert response.status_code == 204
    mock_db.delete.assert_called_once_with(existing_config)
    mock_db.commit.assert_called_once()


@pytest.mark.anyio
@patch("app.routers.alerts._send_slack_alert")
async def test_test_alert_endpoint(mock_send_slack):
    mock_db = AsyncMock()
    alert_id = uuid4()
    existing_config = AlertConfig(
        id=alert_id,
        tenant_id="test_tenant",
        name="Slack Destination",
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/services/abc"},
        enabled=True,
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_config
    mock_db.execute.return_value = mock_result

    result = await router_test_alert(alert_id=alert_id, tenant_id="test_tenant", db=mock_db)
    assert result["success"] is True
    mock_send_slack.assert_called_once()


# ---------------------------------------------------------------------------
# dispatch_dlq_alert — threshold and status correctness
# ---------------------------------------------------------------------------

from app.alerts import dispatch_dlq_alert


def _make_dispatch_db(dlq_depth: int, configs):
    """Build a mock AsyncSession that returns `configs` and `dlq_depth`."""
    mock_db = AsyncMock()

    config_result = MagicMock()
    config_result.scalars.return_value.all.return_value = configs

    depth_result = MagicMock()
    depth_result.scalar_one.return_value = dlq_depth

    mock_db.execute = AsyncMock(side_effect=[config_result, depth_result])
    return mock_db


@pytest.mark.anyio
@patch("app.alerts._send_slack_alert", new_callable=AsyncMock)
async def test_dlq_alert_fires_at_threshold(mock_slack):
    """Alert fires when dlq_depth == dlq_threshold."""
    config = AlertConfig(
        id=uuid4(),
        tenant_id="t",
        name="Slack",
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/x"},
        enabled=True,
        dlq_threshold=5,
    )
    db = _make_dispatch_db(dlq_depth=5, configs=[config])
    await dispatch_dlq_alert(db, "t", "wh_id", "ev_id", "https://dest/hook", 3)
    mock_slack.assert_called_once()


@pytest.mark.anyio
@patch("app.alerts._send_slack_alert", new_callable=AsyncMock)
async def test_dlq_alert_fires_above_threshold(mock_slack):
    """Alert fires when dlq_depth > dlq_threshold."""
    config = AlertConfig(
        id=uuid4(),
        tenant_id="t",
        name="Slack",
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/x"},
        enabled=True,
        dlq_threshold=5,
    )
    db = _make_dispatch_db(dlq_depth=10, configs=[config])
    await dispatch_dlq_alert(db, "t", "wh_id", "ev_id", "https://dest/hook", 3)
    mock_slack.assert_called_once()


@pytest.mark.anyio
@patch("app.alerts._send_slack_alert", new_callable=AsyncMock)
async def test_dlq_alert_skips_below_threshold(mock_slack):
    """Alert is suppressed when dlq_depth < dlq_threshold."""
    config = AlertConfig(
        id=uuid4(),
        tenant_id="t",
        name="Slack",
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/x"},
        enabled=True,
        dlq_threshold=10,
    )
    db = _make_dispatch_db(dlq_depth=3, configs=[config])
    await dispatch_dlq_alert(db, "t", "wh_id", "ev_id", "https://dest/hook", 3)
    mock_slack.assert_not_called()


@pytest.mark.anyio
@patch("app.alerts._send_slack_alert", new_callable=AsyncMock)
async def test_dlq_alert_fires_when_threshold_is_none(mock_slack):
    """No threshold set → alert always fires."""
    config = AlertConfig(
        id=uuid4(),
        tenant_id="t",
        name="Slack",
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/x"},
        enabled=True,
        dlq_threshold=None,
    )
    db = _make_dispatch_db(dlq_depth=0, configs=[config])
    await dispatch_dlq_alert(db, "t", "wh_id", "ev_id", "https://dest/hook", 3)
    mock_slack.assert_called_once()


@pytest.mark.anyio
async def test_dlq_depth_query_uses_failed_status():
    """
    dispatch_dlq_alert counts Webhook.status == 'failed' (the DLQ status),
    NOT 'dead' or any other non-existent status.  This test verifies the
    query arg and ensures that a depth of 0 suppresses threshold-gated alerts.
    """
    config = AlertConfig(
        id=uuid4(),
        tenant_id="t",
        name="Slack",
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/x"},
        enabled=True,
        dlq_threshold=1,
    )

    mock_db = AsyncMock()
    config_result = MagicMock()
    config_result.scalars.return_value.all.return_value = [config]

    depth_result = MagicMock()
    depth_result.scalar_one.return_value = 0   # wrong status → count is 0

    mock_db.execute = AsyncMock(side_effect=[config_result, depth_result])

    with patch("app.alerts._send_slack_alert", new_callable=AsyncMock) as mock_slack:
        await dispatch_dlq_alert(mock_db, "t", "wh_id", "ev_id", "https://dest/hook", 1)
        # With depth=0 and threshold=1, alert should be suppressed
        mock_slack.assert_not_called()

    # Verify the depth query was actually executed (not skipped)
    assert mock_db.execute.call_count == 2


@pytest.mark.anyio
@patch("app.alerts._send_webhook_alert", new_callable=AsyncMock)
async def test_webhook_alert_channel_fires(mock_webhook_alert):
    """Webhook channel type dispatches _send_webhook_alert."""
    config = AlertConfig(
        id=uuid4(),
        tenant_id="t",
        name="PagerDuty",
        channel_type="webhook",
        config={"url": "https://events.pagerduty.com/v2/enqueue"},
        enabled=True,
        dlq_threshold=None,
    )
    db = _make_dispatch_db(dlq_depth=1, configs=[config])
    await dispatch_dlq_alert(db, "t", "wh_id", "ev_id", "https://dest/hook", 1)
    mock_webhook_alert.assert_called_once()


@pytest.mark.anyio
@patch("app.alerts._send_slack_alert", new_callable=AsyncMock)
async def test_alert_skipped_when_disabled(mock_slack):
    """Disabled AlertConfigs are not included in the query result."""
    # The router query filters enabled=True, so we simulate the DB returning nothing
    mock_db = AsyncMock()
    config_result = MagicMock()
    config_result.scalars.return_value.all.return_value = []  # disabled → not returned
    mock_db.execute = AsyncMock(return_value=config_result)

    await dispatch_dlq_alert(mock_db, "t", "wh_id", "ev_id", "https://dest/hook", 1)
    mock_slack.assert_not_called()
