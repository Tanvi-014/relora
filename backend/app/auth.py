"""
Authentication — JWT tokens, password hashing, project role enforcement.
JWT is delivered via httpOnly cookie (hermes_session) or Authorization: Bearer header.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User, Project, ProjectMember

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=settings.JWT_EXPIRY_DAYS))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def _extract_token(request: Request) -> Optional[str]:
    """Pull token from Authorization header or httpOnly cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("hermes_session")


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_active_project(
    current_user: User = Depends(get_current_user),
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> Project:
    if project_id:
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "Project not found")
        mr = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == current_user.id,
            )
        )
        if not mr.scalar_one_or_none():
            raise HTTPException(403, "No access to this project")
        return project
    else:
        result = await db.execute(
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == current_user.id)
            .limit(1)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "No projects found")
        return project


def require_project_role(required_roles: list[str] = None):
    """Dependency factory: checks user has one of required_roles in the project."""
    if required_roles is None:
        required_roles = ["owner", "admin", "viewer"]

    async def checker(
        project_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> ProjectMember:
        if not project_id:
            raise HTTPException(400, "project_id is required")
        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == current_user.id,
            )
        )
        member = result.scalar_one_or_none()
        if not member:
            raise HTTPException(403, "No access to this project")
        if member.role not in required_roles:
            raise HTTPException(403, f"Requires role: {required_roles}")
        return member

    return checker


async def require_api_key(request: Request) -> str:
    """Legacy API-key auth for backward compatibility."""
    tenants = settings.api_key_tenants
    if not tenants:
        return "anonymous"
    key = request.headers.get("X-Hermes-API-Key", "")
    tenant_id = tenants.get(key)
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Hermes-API-Key",
        )
    return tenant_id


async def get_tenant_from_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> str:
    """
    Resolves tenant_id from JWT (SaaS) or X-Hermes-API-Key (legacy).
    For JWT users: returns the project API key scoped to the active project.
    Falls back to 'anonymous' if no auth configured.
    """
    token = _extract_token(request)
    if token:
        payload = decode_access_token(token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                result = await db.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()
                if user:
                    project_id = (
                        request.query_params.get("project_id")
                        or request.headers.get("X-Project-Id")
                    )
                    if project_id:
                        mr = await db.execute(
                            select(ProjectMember).where(
                                ProjectMember.project_id == project_id,
                                ProjectMember.user_id == user.id,
                            )
                        )
                        if mr.scalar_one_or_none():
                            pr = await db.execute(select(Project).where(Project.id == project_id))
                            p = pr.scalar_one_or_none()
                            if p:
                                return p.api_key
                    # Fall back to first project
                    pr = await db.execute(
                        select(Project)
                        .join(ProjectMember, ProjectMember.project_id == Project.id)
                        .where(ProjectMember.user_id == user.id)
                        .limit(1)
                    )
                    p = pr.scalar_one_or_none()
                    if p:
                        return p.api_key

    # Check if X-Hermes-API-Key is a real project api_key in the database.
    # This path is used by programmatic clients (SDKs, curl) that pass the
    # project key directly without going through HERMES_API_KEYS config.
    header_key = request.headers.get("X-Hermes-API-Key", "")
    if header_key:
        pr = await db.execute(select(Project).where(Project.api_key == header_key))
        if pr.scalar_one_or_none():
            return header_key  # valid project key — use it as tenant_id directly

    # Fall back to legacy pre-shared key config (HERMES_API_KEYS env var)
    return await require_api_key(request)
