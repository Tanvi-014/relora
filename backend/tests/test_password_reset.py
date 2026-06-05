"""Tests for the password-reset flow: token helpers, email dispatch, and auth routes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.email import (
    consume_reset_token,
    create_reset_token,
    send_reset_email,
)
from app.models import PasswordResetToken, User


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(email: str = "test@example.com") -> User:
    u = User.__new__(User)
    u.id = uuid4()
    u.email = email
    u.password_hash = "hashed"
    u.email_verified = True
    return u


def _make_reset_token(user_id, raw: str, *, expired=False, used=False) -> PasswordResetToken:
    t = PasswordResetToken.__new__(PasswordResetToken)
    t.id = uuid4()
    t.user_id = user_id
    t.token = raw
    if expired:
        t.expires_at = datetime.now(timezone.utc) - timedelta(hours=2)
    else:
        t.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    t.used_at = datetime.now(timezone.utc) if used else None
    return t


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


# ── create_reset_token ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_reset_token_returns_64_char_string():
    db = AsyncMock()
    user = _make_user()
    token = await create_reset_token(db, user)
    assert isinstance(token, str)
    assert len(token) == 64


@pytest.mark.anyio
async def test_create_reset_token_expires_in_1_hour():
    db = AsyncMock()
    user = _make_user()
    await create_reset_token(db, user)
    record: PasswordResetToken = db.add.call_args[0][0]
    delta = record.expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=59) < delta < timedelta(minutes=61)


@pytest.mark.anyio
async def test_create_reset_token_commits():
    db = AsyncMock()
    user = _make_user()
    await create_reset_token(db, user)
    db.add.assert_called_once()
    db.commit.assert_called_once()


# ── consume_reset_token ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_consume_valid_token_returns_user():
    user = _make_user()
    raw = "v" * 64
    record = _make_reset_token(user.id, raw)

    db = AsyncMock()
    db.execute.side_effect = [
        _scalar_result(record),
        _scalar_result(user),
    ]

    result = await consume_reset_token(db, raw)
    assert result is user
    assert record.used_at is not None
    db.commit.assert_called_once()


@pytest.mark.anyio
async def test_consume_expired_token_returns_none():
    user = _make_user()
    raw = "e" * 64
    record = _make_reset_token(user.id, raw, expired=True)

    db = AsyncMock()
    db.execute.return_value = _scalar_result(record)

    result = await consume_reset_token(db, raw)
    assert result is None
    db.commit.assert_not_called()


@pytest.mark.anyio
async def test_consume_already_used_token_returns_none():
    user = _make_user()
    raw = "u" * 64
    record = _make_reset_token(user.id, raw, used=True)

    db = AsyncMock()
    db.execute.return_value = _scalar_result(record)

    result = await consume_reset_token(db, raw)
    assert result is None


@pytest.mark.anyio
async def test_consume_unknown_token_returns_none():
    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)

    result = await consume_reset_token(db, "notavalidtoken")
    assert result is None


# ── send_reset_email ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_send_reset_returns_false_when_no_api_key():
    with patch("app.email.settings") as s:
        s.RESEND_API_KEY = ""
        result = await send_reset_email("user@example.com", "tok")
    assert result is False


@pytest.mark.anyio
async def test_send_reset_posts_to_resend_with_correct_payload():
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
        result = await send_reset_email("user@example.com", "resettoken")

    assert result is True
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json") or call_kwargs[0][1]
    assert payload["to"] == ["user@example.com"]
    assert "resettoken" in payload["html"]
    assert "/reset-password.html?token=resettoken" in payload["html"]


@pytest.mark.anyio
async def test_send_reset_returns_false_on_non_200():
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
        result = await send_reset_email("user@example.com", "tok")

    assert result is False


@pytest.mark.anyio
async def test_send_reset_returns_false_on_network_error():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("app.email.settings") as s, patch("app.email.httpx.AsyncClient", return_value=mock_client):
        s.RESEND_API_KEY = "re_test_key"
        s.RESEND_FROM_EMAIL = "noreply@example.com"
        s.APP_BASE_URL = "https://example.com"
        result = await send_reset_email("user@example.com", "tok")

    assert result is False


# ── /auth/forgot-password route ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_forgot_password_unknown_email_returns_200():
    """Must not reveal whether the email exists (anti-enumeration)."""
    from fastapi import Request
    from app.routers.auth import forgot_password

    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)
    mock_request = MagicMock(spec=Request)

    with patch("app.routers.auth.settings") as s, \
         patch("app.routers.auth.check_rate_limit", new=AsyncMock()):
        s.RESEND_API_KEY = "re_key"
        s.AUTH_RATE_LIMIT_PER_MINUTE = 10
        resp = await forgot_password(request=mock_request, email="nobody@example.com", db=db)

    assert "sent" in resp["message"]


@pytest.mark.anyio
async def test_forgot_password_no_api_key_raises_503():
    from fastapi import HTTPException, Request
    from app.routers.auth import forgot_password

    mock_request = MagicMock(spec=Request)

    with patch("app.routers.auth.settings") as s, \
         patch("app.routers.auth.check_rate_limit", new=AsyncMock()):
        s.RESEND_API_KEY = ""
        s.AUTH_RATE_LIMIT_PER_MINUTE = 10
        with pytest.raises(HTTPException) as exc_info:
            await forgot_password(request=mock_request, email="user@example.com", db=AsyncMock())

    assert exc_info.value.status_code == 503


# ── /auth/reset-password route ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_reset_password_valid_token_updates_hash():
    from fastapi import Request
    from app.routers.auth import reset_password

    user = _make_user()
    mock_request = MagicMock(spec=Request)

    with patch("app.routers.auth.consume_reset_token", new=AsyncMock(return_value=user)), \
         patch("app.routers.auth.check_rate_limit", new=AsyncMock()), \
         patch("app.routers.auth.get_password_hash", return_value="new_hash"):
        db = AsyncMock()
        resp = await reset_password(
            request=mock_request, token="validtoken", new_password="newpassword1", db=db
        )

    assert "successfully" in resp["message"]
    assert user.password_hash == "new_hash"
    db.commit.assert_called_once()


@pytest.mark.anyio
async def test_reset_password_short_password_raises_400():
    from fastapi import HTTPException, Request
    from app.routers.auth import reset_password

    mock_request = MagicMock(spec=Request)

    with patch("app.routers.auth.check_rate_limit", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await reset_password(
                request=mock_request, token="tok", new_password="short", db=AsyncMock()
            )

    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_reset_password_invalid_token_raises_400():
    from fastapi import HTTPException, Request
    from app.routers.auth import reset_password

    mock_request = MagicMock(spec=Request)

    with patch("app.routers.auth.consume_reset_token", new=AsyncMock(return_value=None)), \
         patch("app.routers.auth.check_rate_limit", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await reset_password(
                request=mock_request, token="badtoken", new_password="newpassword1", db=AsyncMock()
            )

    assert exc_info.value.status_code == 400
