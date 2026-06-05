"""Tests for email verification: token helpers, Resend dispatch, and auth routes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.email import (
    _generate_token,
    consume_verification_token,
    create_verification_token,
    send_verification_email,
)
from app.models import EmailVerificationToken, User


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(email_verified: bool = False) -> User:
    u = User.__new__(User)
    u.id = uuid4()
    u.email = "test@example.com"
    u.password_hash = "hashed"
    u.email_verified = email_verified
    return u


def _make_token(user_id, raw: str, *, expired=False, used=False) -> EmailVerificationToken:
    t = EmailVerificationToken.__new__(EmailVerificationToken)
    t.id = uuid4()
    t.user_id = user_id
    t.token = raw
    if expired:
        t.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    else:
        t.expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    t.used_at = datetime.now(timezone.utc) if used else None
    return t


def _scalar_result(value):
    """Return a mock execute() result that yields `value` from scalar_one_or_none()."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


# ── _generate_token ───────────────────────────────────────────────────────────

def test_generate_token_length():
    tok = _generate_token()
    assert len(tok) == 64  # secrets.token_hex(32) → 64 hex chars


def test_generate_token_unique():
    assert _generate_token() != _generate_token()


# ── create_verification_token ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_verification_token_returns_string():
    db = AsyncMock()
    user = _make_user()
    token = await create_verification_token(db, user)
    assert isinstance(token, str)
    assert len(token) == 64


@pytest.mark.anyio
async def test_create_verification_token_commits():
    db = AsyncMock()
    user = _make_user()
    await create_verification_token(db, user)
    db.add.assert_called_once()
    db.commit.assert_called_once()


@pytest.mark.anyio
async def test_create_verification_token_adds_correct_model():
    db = AsyncMock()
    user = _make_user()
    raw = await create_verification_token(db, user)
    record: EmailVerificationToken = db.add.call_args[0][0]
    assert isinstance(record, EmailVerificationToken)
    assert record.token == raw
    assert record.user_id == user.id
    assert record.expires_at > datetime.now(timezone.utc)


# ── consume_verification_token ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_consume_valid_token_returns_user():
    user = _make_user()
    raw = "a" * 64
    token_record = _make_token(user.id, raw)

    db = AsyncMock()
    db.execute.side_effect = [
        _scalar_result(token_record),
        _scalar_result(user),
    ]

    result = await consume_verification_token(db, raw)
    assert result is user
    assert user.email_verified is True
    assert token_record.used_at is not None
    db.commit.assert_called_once()


@pytest.mark.anyio
async def test_consume_expired_token_returns_none():
    user = _make_user()
    raw = "b" * 64
    token_record = _make_token(user.id, raw, expired=True)

    db = AsyncMock()
    db.execute.return_value = _scalar_result(token_record)

    result = await consume_verification_token(db, raw)
    assert result is None
    db.commit.assert_not_called()


@pytest.mark.anyio
async def test_consume_already_used_token_returns_none():
    user = _make_user()
    raw = "c" * 64
    token_record = _make_token(user.id, raw, used=True)

    db = AsyncMock()
    db.execute.return_value = _scalar_result(token_record)

    result = await consume_verification_token(db, raw)
    assert result is None


@pytest.mark.anyio
async def test_consume_unknown_token_returns_none():
    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)

    result = await consume_verification_token(db, "notavalidtoken")
    assert result is None


# ── send_verification_email ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_send_returns_false_when_no_api_key():
    with patch("app.email.settings") as s:
        s.RESEND_API_KEY = ""
        result = await send_verification_email("user@example.com", "tok")
    assert result is False


@pytest.mark.anyio
async def test_send_posts_to_resend_with_correct_payload():
    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.email.settings") as s, patch("app.email.httpx.AsyncClient", return_value=mock_client):
        s.RESEND_API_KEY = "re_test_key"
        s.RESEND_FROM_EMAIL = "noreply@example.com"
        s.APP_BASE_URL = "https://example.com"
        s.EMAIL_VERIFICATION_EXPIRY_HOURS = 24
        result = await send_verification_email("user@example.com", "mytoken")

    assert result is True
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json") or call_kwargs[0][1]
    assert payload["to"] == ["user@example.com"]
    assert "mytoken" in payload["html"]
    assert "/verify-email.html?token=mytoken" in payload["html"]


@pytest.mark.anyio
async def test_send_returns_false_on_non_200_resend_response():
    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.text = "Unprocessable"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.email.settings") as s, patch("app.email.httpx.AsyncClient", return_value=mock_client):
        s.RESEND_API_KEY = "re_test_key"
        s.RESEND_FROM_EMAIL = "noreply@example.com"
        s.APP_BASE_URL = "https://example.com"
        s.EMAIL_VERIFICATION_EXPIRY_HOURS = 24
        result = await send_verification_email("user@example.com", "tok")

    assert result is False


@pytest.mark.anyio
async def test_send_returns_false_on_network_error():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("app.email.settings") as s, patch("app.email.httpx.AsyncClient", return_value=mock_client):
        s.RESEND_API_KEY = "re_test_key"
        s.RESEND_FROM_EMAIL = "noreply@example.com"
        s.APP_BASE_URL = "https://example.com"
        s.EMAIL_VERIFICATION_EXPIRY_HOURS = 24
        result = await send_verification_email("user@example.com", "tok")

    assert result is False


# ── /auth/verify-email route ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_verify_email_route_success():
    from app.routers.auth import verify_email
    user = _make_user()

    with patch("app.routers.auth.consume_verification_token", new=AsyncMock(return_value=user)):
        resp = await verify_email(token="validtoken", db=AsyncMock())

    assert resp["email_verified"] is True


@pytest.mark.anyio
async def test_verify_email_route_invalid_token_raises_400():
    from fastapi import HTTPException
    from app.routers.auth import verify_email

    with patch("app.routers.auth.consume_verification_token", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await verify_email(token="badtoken", db=AsyncMock())

    assert exc_info.value.status_code == 400


# ── /auth/resend-verification route ──────────────────────────────────────────

@pytest.mark.anyio
async def test_resend_verification_already_verified():
    from app.routers.auth import resend_verification
    user = _make_user(email_verified=True)
    resp = await resend_verification(current_user=user, db=AsyncMock())
    assert "already verified" in resp["message"]


@pytest.mark.anyio
async def test_resend_verification_no_api_key_raises_503():
    from fastapi import HTTPException
    from app.routers.auth import resend_verification
    user = _make_user(email_verified=False)

    with patch("app.routers.auth.settings") as s:
        s.RESEND_API_KEY = ""
        with pytest.raises(HTTPException) as exc_info:
            await resend_verification(current_user=user, db=AsyncMock())

    assert exc_info.value.status_code == 503


# ── /auth/request-verification route (unauthenticated) ───────────────────────

@pytest.mark.anyio
async def test_request_verification_no_api_key_raises_503():
    from fastapi import HTTPException, Request
    from app.routers.auth import request_verification_email

    mock_request = MagicMock(spec=Request)

    with patch("app.routers.auth.settings") as s, \
         patch("app.routers.auth.check_rate_limit", new=AsyncMock()):
        s.RESEND_API_KEY = ""
        s.AUTH_RATE_LIMIT_PER_MINUTE = 10
        with pytest.raises(HTTPException) as exc_info:
            await request_verification_email(
                request=mock_request, email="user@example.com", db=AsyncMock()
            )

    assert exc_info.value.status_code == 503


@pytest.mark.anyio
async def test_request_verification_unknown_email_returns_200():
    from fastapi import Request
    from app.routers.auth import request_verification_email

    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)
    mock_request = MagicMock(spec=Request)

    with patch("app.routers.auth.settings") as s, \
         patch("app.routers.auth.check_rate_limit", new=AsyncMock()):
        s.RESEND_API_KEY = "re_key"
        s.AUTH_RATE_LIMIT_PER_MINUTE = 10
        resp = await request_verification_email(
            request=mock_request, email="nobody@example.com", db=db
        )

    assert "sent" in resp["message"]
