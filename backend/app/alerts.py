"""
Alert dispatcher — fires notifications to configured channels when webhooks
enter the Dead Letter Queue.

Supported channels:
  - Slack (incoming webhook POST)
  - Email (SMTP)
"""

import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Dict, List

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertConfig

logger = logging.getLogger("relora.alerts")


async def dispatch_dlq_alert(
    session: AsyncSession,
    tenant_id: str,
    webhook_id: str,
    event_id: str,
    destination_url: str,
    retry_count: int,
    last_error: str | None = None,
) -> None:
    """
    Called by the worker when a webhook exhausts all retries and enters the DLQ.
    Looks up all enabled AlertConfigs for the tenant and fires notifications.
    """
    result = await session.execute(
        select(AlertConfig).where(
            AlertConfig.tenant_id == tenant_id,
            AlertConfig.enabled == True,
        )
    )
    alert_configs = result.scalars().all()

    if not alert_configs:
        return

    alert_data = {
        "webhook_id": webhook_id,
        "event_id": event_id,
        "destination_url": destination_url,
        "retry_count": retry_count,
        "last_error": last_error or "Unknown error",
        "tenant_id": tenant_id,
    }

    tasks = []
    for config in alert_configs:
        if config.channel_type == "slack":
            tasks.append(_send_slack_alert(config, alert_data))
        elif config.channel_type == "email":
            tasks.append(_send_email_alert(config, alert_data))
        else:
            logger.warning(
                "Unknown alert channel type.",
                extra={
                    "event": "alert.unknown_channel",
                    "channel_type": config.channel_type,
                    "alert_config_id": str(config.id),
                },
            )

    # Fire all alerts concurrently but don't let a single failure block the others
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "Alert delivery failed.",
                extra={
                    "event": "alert.delivery_failed",
                    "alert_config_id": str(alert_configs[i].id),
                    "channel_type": alert_configs[i].channel_type,
                    "error": str(result),
                },
            )


async def _send_slack_alert(config: AlertConfig, data: Dict[str, Any]) -> None:
    """
    Sends a richly formatted Slack message via incoming webhook.
    """
    webhook_url = config.config.get("webhook_url")
    if not webhook_url:
        raise ValueError("Slack alert config is missing 'webhook_url'")

    slack_payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚨 Relora — Webhook Delivery Failed",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"A webhook has exhausted all retries and entered the *Dead Letter Queue*.\n\n"
                        f"*Destination:* `{data['destination_url']}`\n"
                        f"*Event ID:* `{data['event_id']}`\n"
                        f"*Webhook ID:* `{data['webhook_id']}`\n"
                        f"*Attempts:* {data['retry_count']}\n"
                        f"*Last Error:* {data['last_error']}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Tenant: `{data['tenant_id']}` • Open the Relora dashboard to inspect and replay.",
                    }
                ],
            },
        ]
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(webhook_url, json=slack_payload)
        if response.status_code != 200:
            raise RuntimeError(f"Slack returned HTTP {response.status_code}: {response.text[:200]}")

    logger.info(
        "Slack alert sent successfully.",
        extra={
            "event": "alert.slack.sent",
            "alert_config_id": str(config.id),
            "webhook_id": data["webhook_id"],
        },
    )


async def _send_email_alert(config: AlertConfig, data: Dict[str, Any]) -> None:
    """
    Sends an email alert via SMTP. Runs the blocking SMTP calls in a thread
    executor to avoid blocking the async event loop.
    """
    email_config = config.config
    required_fields = ["smtp_host", "smtp_port", "from", "to"]
    for field in required_fields:
        if field not in email_config:
            raise ValueError(f"Email alert config is missing '{field}'")

    subject = f"🚨 Relora Alert — Webhook Failed: {data['destination_url']}"

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; background: #121214; color: #f4f4f5; padding: 24px; border-radius: 8px;">
        <h2 style="color: #ef4444; margin-bottom: 16px;">🚨 Webhook Delivery Failed</h2>
        <p style="color: #a1a1aa; margin-bottom: 20px;">
            A webhook has exhausted all retries and entered the Dead Letter Queue.
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
            <tr>
                <td style="padding: 8px 12px; color: #71717a; border-bottom: 1px solid #27272a;">Destination</td>
                <td style="padding: 8px 12px; color: #f4f4f5; border-bottom: 1px solid #27272a; font-family: monospace;">{data['destination_url']}</td>
            </tr>
            <tr>
                <td style="padding: 8px 12px; color: #71717a; border-bottom: 1px solid #27272a;">Event ID</td>
                <td style="padding: 8px 12px; color: #f4f4f5; border-bottom: 1px solid #27272a; font-family: monospace;">{data['event_id']}</td>
            </tr>
            <tr>
                <td style="padding: 8px 12px; color: #71717a; border-bottom: 1px solid #27272a;">Webhook ID</td>
                <td style="padding: 8px 12px; color: #f4f4f5; border-bottom: 1px solid #27272a; font-family: monospace;">{data['webhook_id']}</td>
            </tr>
            <tr>
                <td style="padding: 8px 12px; color: #71717a; border-bottom: 1px solid #27272a;">Attempts</td>
                <td style="padding: 8px 12px; color: #f4f4f5; border-bottom: 1px solid #27272a;">{data['retry_count']}</td>
            </tr>
            <tr>
                <td style="padding: 8px 12px; color: #71717a; border-bottom: 1px solid #27272a;">Last Error</td>
                <td style="padding: 8px 12px; color: #ef4444; border-bottom: 1px solid #27272a;">{data['last_error']}</td>
            </tr>
        </table>
        <p style="color: #71717a; font-size: 12px;">
            Open the Relora dashboard to inspect the payload and replay delivery.
        </p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_config["from"]
    msg["To"] = email_config["to"]
    msg.attach(MIMEText(html_body, "html"))

    # Run blocking SMTP in a thread so we don't stall the event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _smtp_send, email_config, msg)

    logger.info(
        "Email alert sent successfully.",
        extra={
            "event": "alert.email.sent",
            "alert_config_id": str(config.id),
            "webhook_id": data["webhook_id"],
            "to": email_config["to"],
        },
    )


def _smtp_send(email_config: Dict[str, Any], msg: MIMEMultipart) -> None:
    """Blocking SMTP send — called inside run_in_executor."""
    smtp_host = email_config["smtp_host"]
    smtp_port = int(email_config["smtp_port"])
    username = email_config.get("username")
    password = email_config.get("password")
    use_tls = email_config.get("use_tls", True)

    if use_tls:
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
    else:
        server = smtplib.SMTP(smtp_host, smtp_port)

    if username and password:
        server.login(username, password)

    server.send_message(msg)
    server.quit()
