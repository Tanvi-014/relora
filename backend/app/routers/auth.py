import logging

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
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
    return {"message": "Registered", "user_id": str(user.id)}


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
