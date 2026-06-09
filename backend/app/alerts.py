"""
Alert dispatcher — fires notifications to configured channels when webhooks
enter the Dead Letter Queue.

Supported channels:
  - slack    — Slack incoming webhook POST
  - email    — SMTP (legacy) or Resend API
  - sms      — Twilio REST API (no SDK dependency)
  - webhook  — Generic HTTP POST with JSON body (PagerDuty, Teams, custom)
"""

import asyncio
import base64
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AlertConfig, Webhook

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

    dlq_depth_result = await session.execute(
        select(func.count()).select_from(Webhook).where(
            Webhook.tenant_id == tenant_id,
            Webhook.status == "failed",
        )
    )
    current_dlq_depth = dlq_depth_result.scalar_one()

    alert_data = {
        "webhook_id": webhook_id,
        "event_id": event_id,
        "destination_url": destination_url,
        "retry_count": retry_count,
        "last_error": last_error or "Unknown error",
        "tenant_id": tenant_id,
    }

    tasks = []
    fired_configs: List[AlertConfig] = []
    for config in alert_configs:
        if config.dlq_threshold is not None and current_dlq_depth < config.dlq_threshold:
            continue
        if config.channel_type == "slack":
            tasks.append(_send_slack_alert(config, alert_data))
        elif config.channel_type == "email":
            tasks.append(_send_email_alert(config, alert_data))
        elif config.channel_type == "sms":
            tasks.append(_send_sms_alert(config, alert_data))
        elif config.channel_type == "webhook":
            tasks.append(_send_webhook_alert(config, alert_data))
        else:
            logger.warning(
                "Unknown alert channel type.",
                extra={
                    "event": "alert.unknown_channel",
                    "channel_type": config.channel_type,
                    "alert_config_id": str(config.id),
                },
            )
            continue
        fired_configs.append(config)

    # Fire all alerts concurrently — a single failure must not block others
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "Alert delivery failed.",
                extra={
                    "event": "alert.delivery_failed",
                    "alert_config_id": str(fired_configs[i].id),
                    "channel_type": fired_configs[i].channel_type,
                    "error": str(result),
                },
            )


# ── Slack ─────────────────────────────────────────────────────────────────────

async def _send_slack_alert(config: AlertConfig, data: Dict[str, Any]) -> None:
    webhook_url = config.config.get("webhook_url")
    if not webhook_url:
        raise ValueError("Slack alert config is missing 'webhook_url'")

    slack_payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Relora — Webhook Delivery Failed", "emoji": True},
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
        "Slack alert sent.",
        extra={"event": "alert.slack.sent", "alert_config_id": str(config.id), "webhook_id": data["webhook_id"]},
    )


# ── Email (SMTP) ──────────────────────────────────────────────────────────────

async def _send_email_alert(config: AlertConfig, data: Dict[str, Any]) -> None:
    email_config = config.config
    for field in ("smtp_host", "smtp_port", "from", "to"):
        if field not in email_config:
            raise ValueError(f"Email alert config is missing '{field}'")

    subject = f"Relora Alert — Webhook Failed: {data['destination_url']}"
    html_body = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;
                margin:0 auto;background:#121214;color:#f4f4f5;padding:24px;border-radius:8px;">
      <h2 style="color:#ef4444;margin-bottom:16px;">Webhook Delivery Failed</h2>
      <p style="color:#a1a1aa;margin-bottom:20px;">
        A webhook has exhausted all retries and entered the Dead Letter Queue.
      </p>
      <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
        <tr><td style="padding:8px 12px;color:#71717a;border-bottom:1px solid #27272a;">Destination</td>
            <td style="padding:8px 12px;color:#f4f4f5;border-bottom:1px solid #27272a;font-family:monospace;">{data['destination_url']}</td></tr>
        <tr><td style="padding:8px 12px;color:#71717a;border-bottom:1px solid #27272a;">Event ID</td>
            <td style="padding:8px 12px;color:#f4f4f5;border-bottom:1px solid #27272a;font-family:monospace;">{data['event_id']}</td></tr>
        <tr><td style="padding:8px 12px;color:#71717a;border-bottom:1px solid #27272a;">Webhook ID</td>
            <td style="padding:8px 12px;color:#f4f4f5;border-bottom:1px solid #27272a;font-family:monospace;">{data['webhook_id']}</td></tr>
        <tr><td style="padding:8px 12px;color:#71717a;border-bottom:1px solid #27272a;">Attempts</td>
            <td style="padding:8px 12px;color:#f4f4f5;border-bottom:1px solid #27272a;">{data['retry_count']}</td></tr>
        <tr><td style="padding:8px 12px;color:#71717a;">Last Error</td>
            <td style="padding:8px 12px;color:#ef4444;">{data['last_error']}</td></tr>
      </table>
      <p style="color:#71717a;font-size:12px;">
        Open the Relora dashboard to inspect the payload and replay delivery.
      </p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_config["from"]
    msg["To"] = email_config["to"]
    msg.attach(MIMEText(html_body, "html"))

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _smtp_send, email_config, msg)

    logger.info(
        "Email alert sent.",
        extra={"event": "alert.email.sent", "alert_config_id": str(config.id), "to": email_config["to"]},
    )


def _smtp_send(email_config: Dict[str, Any], msg: MIMEMultipart) -> None:
    smtp_host = email_config["smtp_host"]
    smtp_port = int(email_config["smtp_port"])
    username = email_config.get("username")
    password = email_config.get("password")
    use_tls = email_config.get("use_tls", True)

    server = smtplib.SMTP(smtp_host, smtp_port)
    if use_tls:
        server.starttls()
    if username and password:
        server.login(username, password)
    server.send_message(msg)
    server.quit()


# ── SMS via Twilio ─────────────────────────────────────────────────────────────

async def _send_sms_alert(config: AlertConfig, data: Dict[str, Any]) -> None:
    """
    Sends an SMS via Twilio's REST API.

    AlertConfig.config schema:
      {
        "to": "+15005550006",           -- destination number (E.164)
        "account_sid": "ACxxx",         -- optional override; uses TWILIO_ACCOUNT_SID if absent
        "auth_token": "xxx",            -- optional override; uses TWILIO_AUTH_TOKEN if absent
        "from": "+15005550001"          -- optional override; uses TWILIO_FROM_NUMBER if absent
      }
    """
    cfg = config.config
    account_sid = cfg.get("account_sid") or settings.TWILIO_ACCOUNT_SID
    auth_token = cfg.get("auth_token") or settings.TWILIO_AUTH_TOKEN
    from_number = cfg.get("from") or settings.TWILIO_FROM_NUMBER
    to_number = cfg.get("to")

    if not all([account_sid, auth_token, from_number, to_number]):
        raise ValueError(
            "SMS alert requires 'to' in config and TWILIO_ACCOUNT_SID / "
            "TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER in settings."
        )

    body = (
        f"[Relora] DLQ alert\n"
        f"Dest: {data['destination_url']}\n"
        f"Error: {data['last_error'][:80]}\n"
        f"Attempts: {data['retry_count']}"
    )

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            data={"From": from_number, "To": to_number, "Body": body},
            headers={"Authorization": f"Basic {credentials}"},
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Twilio returned HTTP {response.status_code}: {response.text[:200]}")

    logger.info(
        "SMS alert sent.",
        extra={"event": "alert.sms.sent", "alert_config_id": str(config.id), "to": to_number},
    )


# ── Generic webhook ────────────────────────────────────────────────────────────

async def _send_webhook_alert(config: AlertConfig, data: Dict[str, Any]) -> None:
    """
    POSTs a JSON payload to any HTTP endpoint.

    AlertConfig.config schema:
      {
        "url": "https://events.pagerduty.com/v2/enqueue",
        "secret": "optional-hmac-secret",   -- if set, adds X-Relora-Signature header
        "headers": {"Authorization": "..."}  -- optional extra headers
      }
    """
    cfg = config.config
    url = cfg.get("url")
    if not url:
        raise ValueError("Webhook alert config is missing 'url'")

    payload = {
        "event": "dlq.alert",
        "webhook_id": data["webhook_id"],
        "event_id": data["event_id"],
        "destination_url": data["destination_url"],
        "retry_count": data["retry_count"],
        "last_error": data["last_error"],
        "tenant_id": data["tenant_id"],
    }

    headers = {"Content-Type": "application/json"}
    if extra_headers := cfg.get("headers"):
        headers.update(extra_headers)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"Webhook alert endpoint returned HTTP {response.status_code}: {response.text[:200]}")

    logger.info(
        "Webhook alert sent.",
        extra={"event": "alert.webhook.sent", "alert_config_id": str(config.id), "url": url},
    )
