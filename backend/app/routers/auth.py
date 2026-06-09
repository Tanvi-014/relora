import asyncio
import hashlib
import logging

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.email import (
    consume_reset_token,
    consume_verification_token,
    create_reset_token,
    create_verification_token,
    send_reset_email,
    send_verification_email,
)
from app.models import User
from app.rate_limit import check_rate_limit

logger = logging.getLogger("relora.api")

router = APIRouter()

EXCLUDED_INGEST_HEADERS = {
    "host", "connection", "content-length", "accept-encoding",
    "user-agent", "x-real-ip", "x-forwarded-for",
    "x-forwarded-proto", "x-forwarded-port",
}


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="relora_session",
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.JWT_EXPIRY_DAYS * 86400,
        domain=settings.COOKIE_DOMAIN or None,
    )


async def _get_token_from_request(
    request: Request,
    relora_session: Optional[str] = Cookie(default=None),
) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return relora_session


@router.post("/api/v1/auth/register", status_code=201)
async def register(
    request: Request,
    email: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, "anonymous", db, max_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE)
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email address is required")
    if not password or len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")
    user = User(email=email, password_hash=get_password_hash(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("User registered", extra={"event": "user.registered", "user_id": str(user.id)})

    # Fire verification email in the background — never blocks registration
    if settings.RESEND_API_KEY:
        token = await create_verification_token(db, user)
        asyncio.create_task(send_verification_email(user.email, token))

    return {
        "message": "Registered successfully. Check your email to verify your address.",
        "user_id": str(user.id),
        "email_verified": user.email_verified,
    }


@router.post("/api/v1/auth/login")
async def login(
    request: Request,
    email: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, "anonymous", db, max_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    if settings.EMAIL_VERIFICATION_REQUIRED and not user.email_verified:
        raise HTTPException(
            403,
            "Email address not verified. Check your inbox or call POST /api/v1/auth/resend-verification.",
        )

    token = create_access_token({"sub": str(user.id)})
    response = JSONResponse(content={"access_token": token, "token_type": "bearer", "user": user.to_dict()})
    _set_auth_cookie(response, token)
    logger.info("User logged in", extra={"event": "user.login", "user_id": str(user.id)})
    return response


@router.post("/api/v1/auth/logout")
async def logout():
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("relora_session")
    return response


@router.get("/api/v1/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user.to_dict()


@router.get("/api/v1/auth/verify-email")
async def verify_email(
    token: str = Query(..., description="Verification token from the email link"),
    db: AsyncSession = Depends(get_db),
):
    """Verify an email address using the token sent during registration."""
    user = await consume_verification_token(db, token)
    if not user:
        raise HTTPException(400, "Invalid or expired verification link. Request a new one.")
    logger.info(
        "Email verified",
        extra={"event": "user.email_verified", "user_id": str(user.id)},
    )
    return {"message": "Email verified successfully.", "email_verified": True}


@router.post("/api/v1/auth/request-verification")
async def request_verification_email(
    request: Request,
    email: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    """Request a fresh verification email by address (no auth required, rate-limited).

    Always returns 200 to avoid email enumeration — the caller cannot tell
    whether the address exists or not.
    """
    await check_rate_limit(request, "anonymous", db, max_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE)
    _email_key = "email:" + hashlib.sha256(email.lower().encode()).hexdigest()[:16]
    await check_rate_limit(request, _email_key, db, max_per_minute=5)
    if not settings.RESEND_API_KEY:
        raise HTTPException(503, "Email sending is not configured on this server.")
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user and not user.email_verified:
        token = await create_verification_token(db, user)
        asyncio.create_task(send_verification_email(user.email, token))
    return {"message": "If that address exists and is unverified, a new link has been sent."}


@router.post("/api/v1/auth/forgot-password")
async def forgot_password(
    request: Request,
    email: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    """Request a password-reset link by email address (no auth required, rate-limited).

    Always returns 200 to avoid email enumeration — the caller cannot tell
    whether the address exists or not.
    """
    await check_rate_limit(request, "anonymous", db, max_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE)
    _email_key = "email:" + hashlib.sha256(email.lower().encode()).hexdigest()[:16]
    await check_rate_limit(request, _email_key, db, max_per_minute=5)
    if not settings.RESEND_API_KEY:
        raise HTTPException(503, "Email sending is not configured on this server.")
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        token = await create_reset_token(db, user)
        asyncio.create_task(send_reset_email(user.email, token))
    return {"message": "If that address is registered, a password-reset link has been sent."}


@router.post("/api/v1/auth/reset-password")
async def reset_password(
    request: Request,
    token: str = Body(..., embed=True),
    new_password: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    """Consume a password-reset token and set a new password."""
    await check_rate_limit(request, "anonymous", db, max_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE)
    if not new_password or len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    user = await consume_reset_token(db, token)
    if not user:
        raise HTTPException(400, "Invalid or expired reset link. Request a new one.")
    user.password_hash = get_password_hash(new_password)
    await db.commit()
    logger.info(
        "Password reset completed",
        extra={"event": "user.password_reset", "user_id": str(user.id)},
    )
    return {"message": "Password updated successfully. You can now sign in with your new password."}


@router.post("/api/v1/auth/resend-verification")
async def resend_verification(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-send the verification email for the authenticated user."""
    if current_user.email_verified:
        return {"message": "Email is already verified."}
    if not settings.RESEND_API_KEY:
        raise HTTPException(503, "Email sending is not configured on this server.")
    token = await create_verification_token(db, current_user)
    sent = await send_verification_email(current_user.email, token)
    if not sent:
        raise HTTPException(503, "Failed to send verification email. Try again later.")
    return {"message": "Verification email sent. Check your inbox."}
