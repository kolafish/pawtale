from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
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
from app.services.projects.export import (
    ProjectExportFormat,
    content_disposition,
    export_project_data,
    render_export_file,
)


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


@router.get("/{project_id}/export/download")
async def download_project_export(
    project_id: str,
    format: ProjectExportFormat = Query(ProjectExportFormat.TXT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = await export_project_data(db, project_id, current_user.id)
    export_file = render_export_file(data, format)
    return Response(
        content=export_file.content,
        media_type=export_file.media_type,
        headers={"Content-Disposition": content_disposition(export_file.filename)},
    )
