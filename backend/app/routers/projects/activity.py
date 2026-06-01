from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.projects import (
    ProjectExportResponse,
    WritingSessionCreate,
    WritingSessionResponse,
)
from app.services.projects import activity as project_activity


router = APIRouter()


@router.get("/{project_id}/sessions", response_model=list[WritingSessionResponse])
async def list_sessions(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_activity.list_sessions(db, project_id, current_user.id)


@router.post("/{project_id}/sessions", response_model=WritingSessionResponse, status_code=201)
async def create_session(
    project_id: str,
    req: WritingSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_activity.create_session(db, project_id, current_user.id, req)


@router.get("/{project_id}/export", response_model=ProjectExportResponse)
async def export_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_activity.export_project(db, project_id, current_user.id)
