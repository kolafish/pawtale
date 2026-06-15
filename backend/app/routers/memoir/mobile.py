from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir import (
    MemoirIdentityResponse,
    MemoirProjectCreate,
    MemoirProjectListResponse,
    MemoirProjectUpdate,
)
from app.schemas.projects import ProjectResponse
from app.services.memoir import projects as memoir_projects


router = APIRouter()


@router.get("/me", response_model=MemoirIdentityResponse)
async def get_mobile_memoir_identity(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    projects = await memoir_projects.mobile_project_list(db, current_user.id)
    return MemoirIdentityResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        avatar_url=current_user.avatar_url,
        created_at=current_user.created_at,
        active_project_id=projects.active_project_id,
        active_project=projects.active_project,
        projects=projects.projects,
    )


@router.get("/projects", response_model=MemoirProjectListResponse)
async def list_mobile_memoir_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.mobile_project_list(db, current_user.id)


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_mobile_memoir_project(
    req: MemoirProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.create_mobile_memoir_project(db, current_user.id, req)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_mobile_memoir_project(
    project_id: str,
    req: MemoirProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.update_mobile_memoir_project(
        db,
        current_user.id,
        project_id,
        req,
    )


@router.post("/projects/{project_id}/activate", response_model=MemoirProjectListResponse)
async def activate_mobile_memoir_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.activate_mobile_memoir_project(
        db,
        current_user.id,
        project_id,
    )
