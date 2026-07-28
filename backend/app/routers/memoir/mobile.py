from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import commit_or_rollback, get_db
from app.models.users import User
from app.schemas.memoir_projects import (
    MemoirIdentityResponse,
    MemoirProjectCreate,
    MemoirProjectListResponse,
    MemoirProjectUpdate,
)
from app.schemas.memoir_snapshots import (
    MemoirBookSnapshotResponse,
    MemoirHomeSnapshotResponse,
)
from app.schemas.memoir_topic_map import (
    MemoirTopicMapItemResponse,
    MemoirTopicMapResolutionRequest,
    MemoirTopicMapResponse,
)
from app.schemas.projects import ProjectResponse
from app.services.memoir import projects as memoir_projects
from app.services.memoir import read_snapshots as memoir_read_snapshots
from app.services.memoir.access import load_memoir_project
from app.services.memoir.narrative import topic_map


router = APIRouter()


@router.get("/me", response_model=MemoirIdentityResponse)
async def get_mobile_memoir_identity(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.memoir_identity_response(db, current_user)


@router.get("/home-snapshot", response_model=MemoirHomeSnapshotResponse)
async def get_mobile_memoir_home_snapshot(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_read_snapshots.home_snapshot(db, current_user)


@router.get("/book-snapshot", response_model=MemoirBookSnapshotResponse)
async def get_mobile_memoir_book_snapshot(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_read_snapshots.book_snapshot(db, current_user)


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


@router.delete("/projects/{project_id}", status_code=204)
async def delete_mobile_memoir_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await memoir_projects.delete_mobile_memoir_project(
        db,
        current_user.id,
        project_id,
    )


@router.get(
    "/projects/{project_id}/topic-map",
    response_model=MemoirTopicMapResponse,
)
async def get_mobile_memoir_topic_map(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await load_memoir_project(db, project_id, current_user.id)
    try:
        return await topic_map.get_project_topic_map(db, project_id=project_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/projects/{project_id}/topic-map/items/{item_id}/resolution",
    response_model=MemoirTopicMapItemResponse,
)
async def update_mobile_memoir_topic_map_resolution(
    project_id: str,
    item_id: str,
    req: MemoirTopicMapResolutionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await load_memoir_project(db, project_id, current_user.id)
    try:
        async with commit_or_rollback(db):
            return await topic_map.update_user_resolution(
                db,
                project_id=project_id,
                item_id=item_id,
                user_resolution=req.user_resolution,
                actor_user_id=current_user.id,
            )
    except ValueError as exc:
        status_code = 404 if "does not belong" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
