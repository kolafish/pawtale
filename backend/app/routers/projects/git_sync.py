from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.projects import (
    GitSyncStatusResponse,
    ProjectGitSyncConfigRequest,
    ProjectGitSyncConfigResponse,
    ProjectGitSyncRunResponse,
)
from app.services.projects import git_sync as project_git_sync


router = APIRouter()


@router.get("/{project_id}/git-sync", response_model=GitSyncStatusResponse)
async def get_git_sync_status(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_git_sync.get_status(db, project_id, current_user.id)


@router.put("/{project_id}/git-sync", response_model=ProjectGitSyncConfigResponse)
async def save_git_sync_config(
    project_id: str,
    req: ProjectGitSyncConfigRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_git_sync.save_config(db, project_id, current_user.id, req)


@router.delete("/{project_id}/git-sync", status_code=204)
async def delete_git_sync_config(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await project_git_sync.delete_config(db, project_id, current_user.id)


@router.post("/{project_id}/git-sync/run", response_model=ProjectGitSyncRunResponse)
async def run_git_sync(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_git_sync.run_sync(db, project_id, current_user.id)
