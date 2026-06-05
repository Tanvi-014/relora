"""
Transactional email via Resend API.

Handles:
  - Email address verification on registration
  - Password-reset links (future)

httpx is already a dependency so no new package is needed.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EmailVerificationToken, PasswordResetToken, User

logger = logging.getLogger("relora.email")

_RESEND_SEND_URL = "https://api.resend.com/emails"


# ── Token helpers ─────────────────────────────────────────────────────────────

def _generate_token() -> str:
    return secrets.token_hex(32)  # 64 hex chars


async def create_verification_token(db: AsyncSession, user: User) -> str:
    """Create and persist a fresh verification token for *user*. Returns the raw token."""
    token = _generate_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.EMAIL_VERIFICATION_EXPIRY_HOURS)
    record = EmailVerificationToken(
        user_id=user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()
    return token


async def consume_verification_token(db: AsyncSession, raw_token: str) -> Optional[User]:
    """
    Validate *raw_token*, mark it used, mark the user verified.
    Returns the User on success, None if the token is invalid/expired/already used.
    """
    result = await db.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token == raw_token)
    )
    record = result.scalar_one_or_none()
    if not record:
        return None
    if record.used_at is not None:
        return None
    if datetime.now(timezone.utc) > record.expires_at:
        return None

    record.used_at = datetime.now(timezone.utc)

    user_result = await db.execute(select(User).where(User.id == record.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        return None

    user.email_verified = True
    await db.commit()
    return user


# ── Password reset token helpers ─────────────────────────────────────────────

async def create_reset_token(db: AsyncSession, user: User) -> str:
    """Create and persist a password-reset token for *user*. Returns the raw token."""
    token = _generate_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    record = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()
    return token


async def consume_reset_token(db: AsyncSession, raw_token: str) -> Optional[User]:
    """
    Validate *raw_token*, mark it used.
    Returns the User on success, None if invalid/expired/already used.
    Does NOT change the password — caller is responsible.
    """
    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token == raw_token)
    )
    record = result.scalar_one_or_none()
    if not record:
        return None
    if record.used_at is not None:
        return None
    if datetime.now(timezone.utc) > record.expires_at:
        return None

    record.used_at = datetime.now(timezone.utc)

    user_result = await db.execute(select(User).where(User.id == record.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        return None

    await db.commit()
    return user


# ── Resend API send ───────────────────────────────────────────────────────────

async def send_verification_email(to_email: str, token: str) -> bool:
    """
    Send the email verification link via Resend.
    Returns True on success, False if Resend is not configured or the call fails.
    Failures are logged but never raised — a broken mailer must not block registration.
    """
    if not settings.RESEND_API_KEY:
        logger.debug("RESEND_API_KEY not set — skipping verification email for %s", to_email)
        return False

    verify_url = f"{settings.APP_BASE_URL.rstrip('/')}/verify-email.html?token={token}"

    html_body = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;
                margin:0 auto;background:#18181b;color:#f4f4f5;padding:32px;border-radius:8px;">
      <h2 style="color:#f4f4f5;margin:0 0 8px;">Verify your email address</h2>
      <p style="color:#a1a1aa;margin:0 0 24px;line-height:1.6;">
        Click the button below to confirm your Relora account.
        This link expires in {settings.EMAIL_VERIFICATION_EXPIRY_HOURS} hours.
      </p>
      <a href="{verify_url}"
         style="display:inline-block;background:#3b82f6;color:#fff;padding:12px 24px;
                border-radius:6px;text-decoration:none;font-weight:600;">
        Verify email address
      </a>
      <p style="color:#52525b;font-size:12px;margin:24px 0 0;">
        Or copy this link: <span style="color:#a1a1aa;">{verify_url}</span>
      </p>
    </div>
    """

    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": "Verify your Relora email address",
        "html": html_body,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                _RESEND_SEND_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            )
        if r.status_code not in (200, 201):
            logger.warning(
                "Resend returned HTTP %s when sending verification email to %s: %s",
                r.status_code, to_email, r.text[:200],
            )
            return False
        logger.info("Verification email sent to %s", to_email, extra={"event": "email.verification.sent"})
        return True
    except Exception as exc:
        logger.warning("Failed to send verification email to %s: %s", to_email, exc)
        return False


async def send_reset_email(to_email: str, token: str) -> bool:
    """
    Send a password-reset link via Resend.
    Returns True on success, False if Resend is not configured or the call fails.
    """
    if not settings.RESEND_API_KEY:
        logger.debug("RESEND_API_KEY not set — skipping password reset email for %s", to_email)
        return False

    reset_url = f"{settings.APP_BASE_URL.rstrip('/')}/reset-password.html?token={token}"

    html_body = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;
                margin:0 auto;background:#18181b;color:#f4f4f5;padding:32px;border-radius:8px;">
      <h2 style="color:#f4f4f5;margin:0 0 8px;">Reset your password</h2>
      <p style="color:#a1a1aa;margin:0 0 24px;line-height:1.6;">
        Click the button below to choose a new password for your Relora account.
        This link expires in 1 hour. If you did not request a reset, ignore this email.
      </p>
      <a href="{reset_url}"
         style="display:inline-block;background:#3b82f6;color:#fff;padding:12px 24px;
                border-radius:6px;text-decoration:none;font-weight:600;">
        Reset password
      </a>
      <p style="color:#52525b;font-size:12px;margin:24px 0 0;">
        Or copy this link: <span style="color:#a1a1aa;">{reset_url}</span>
      </p>
    </div>
    """

    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": "Reset your Relora password",
        "html": html_body,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                _RESEND_SEND_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            )
        if r.status_code not in (200, 201):
            logger.warning(
                "Resend returned HTTP %s when sending reset email to %s: %s",
                r.status_code, to_email, r.text[:200],
            )
            return False
        logger.info("Password reset email sent to %s", to_email, extra={"event": "email.reset.sent"})
        return True
    except Exception as exc:
        logger.warning("Failed to send password reset email to %s: %s", to_email, exc)
        return False
