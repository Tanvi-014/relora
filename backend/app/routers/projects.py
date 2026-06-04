import uuid as _uuid_mod

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.audit import audit
from app.auth import get_current_user, require_project_role
from app.db import get_db
from app.models import Project, ProjectMember, User

router = APIRouter()


async def _get_project_by_api_key(db: AsyncSession, api_key: str) -> Project:
    result = await db.execute(select(Project).where(Project.api_key == api_key))
    project = result.scalar_one_or_none()
    if not project:
        # For anonymous / legacy tenants create a virtual project
        return Project(id=_uuid_mod.uuid4(), name="default", api_key=api_key)
    return project


@router.get("/api/v1/projects")
async def list_projects(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == current_user.id)
        .order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    out = []
    for p in projects:
        mr = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == p.id,
                ProjectMember.user_id == current_user.id,
            )
        )
        m = mr.scalar_one_or_none()
        d = p.to_dict()
        d["role"] = m.role if m else None
        out.append(d)
    return out


@router.post("/api/v1/projects", status_code=201)
async def create_project(
    request: Request,
    name: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = Project(name=name, api_key=f"hk_live_{_uuid_mod.uuid4().hex}")
    db.add(project)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=current_user.id, role="owner"))
    await audit(db, request, str(current_user.id), "CREATE", "project", str(project.id), after={"name": name})
    await db.commit()
    await db.refresh(project)
    d = project.to_dict()
    d["role"] = "owner"
    return d


@router.get("/api/v1/projects/{project_id}")
async def get_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
    m = mr.scalar_one_or_none()
    if not m:
        raise HTTPException(403, "No access")
    d = project.to_dict()
    d["role"] = m.role
    return d


@router.delete("/api/v1/projects/{project_id}", status_code=204)
async def delete_project(
    request: Request,
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    checker = require_project_role(["owner"])
    await checker(project_id=str(project_id), current_user=current_user, db=db)
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")
    await audit(db, request, str(current_user.id), "DELETE", "project", str(project_id), before=project.to_dict())
    await db.delete(project)
    await db.commit()
    return Response(status_code=204)


@router.get("/api/v1/projects/{project_id}/members")
async def list_members(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mr = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
        )
    )
    if not mr.scalar_one_or_none():
        raise HTTPException(403, "No access")
    result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id))
    return [m.to_dict() for m in result.scalars().all()]


@router.post("/api/v1/projects/{project_id}/members", status_code=201)
async def add_member(
    project_id: UUID,
    email: str = Body(..., embed=True),
    role: str = Body("viewer", embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if role not in ("owner", "admin", "viewer"):
        raise HTTPException(400, "Role must be owner, admin, or viewer")
    checker = require_project_role(["owner", "admin"])
    await checker(project_id=str(project_id), current_user=current_user, db=db)
    ur = await db.execute(select(User).where(User.email == email))
    user = ur.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Already a member")
    m = ProjectMember(project_id=project_id, user_id=user.id, role=role)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m.to_dict()


@router.post("/api/v1/projects/{project_id}/rotate-key")
async def rotate_api_key(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new API key for the project, invalidating the old one immediately.
    Requires owner role. Returns the new key — show it once, it cannot be retrieved again.
    """
    checker = require_project_role(["owner"])
    await checker(project_id=str(project_id), current_user=current_user, db=db)
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")
    new_key = f"hk_live_{_uuid_mod.uuid4().hex}"
    project.api_key = new_key
    await db.commit()
    await db.refresh(project)
    return {"api_key": new_key, "message": "API key rotated. Update all services using the old key immediately."}


@router.delete("/api/v1/projects/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: UUID,
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    checker = require_project_role(["owner"])
    await checker(project_id=str(project_id), current_user=current_user, db=db)
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Member not found")
    await db.delete(m)
    await db.commit()
    return Response(status_code=204)
