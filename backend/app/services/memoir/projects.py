from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.projects.templates import initialize_project_template
from app.domain.projects.types import ProjectType
from app.models.projects import DocumentGroup, Project
from app.schemas.memoir import MemoirProjectCreate, MemoirProjectListResponse, MemoirProjectUpdate
from app.schemas.projects import ProjectResponse
from app.services import project_documents as project_docs
from app.services.projects.access import load_project


DEFAULT_MEMOIR_PROJECT_TITLE = "我的回忆录"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _memoir_project_options():
    return (
        selectinload(Project.groups).selectinload(DocumentGroup.documents),
        selectinload(Project.references),
    )


async def _load_memoir_project_response(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> ProjectResponse:
    project = await load_project(db, project_id, user_id)
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")
    return project_docs.project_response(project)


async def list_mobile_memoir_project_responses(
    db: AsyncSession,
    user_id: str,
) -> list[ProjectResponse]:
    result = await db.execute(
        select(Project)
        .where(Project.user_id == user_id, Project.type == ProjectType.MEMOIR.value)
        .options(*_memoir_project_options())
        .order_by(Project.updated_at.desc(), Project.created_at.desc())
    )
    return [project_docs.project_response(project) for project in result.scalars()]


async def active_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
) -> ProjectResponse:
    projects = await list_mobile_memoir_project_responses(db, user_id)
    if projects:
        return projects[0]
    return await get_or_create_mobile_memoir_project(db, user_id)


async def get_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> ProjectResponse:
    return await _load_memoir_project_response(db, project_id, user_id)


async def mobile_project_list(
    db: AsyncSession,
    user_id: str,
) -> MemoirProjectListResponse:
    active_project = await active_mobile_memoir_project(db, user_id)
    projects = await list_mobile_memoir_project_responses(db, user_id)
    return MemoirProjectListResponse(
        active_project_id=active_project.id,
        active_project=active_project,
        projects=projects,
    )


async def get_or_create_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
) -> ProjectResponse:
    result = await db.execute(
        select(Project)
        .where(Project.user_id == user_id, Project.type == ProjectType.MEMOIR.value)
        .options(*_memoir_project_options())
        .order_by(Project.updated_at.desc(), Project.created_at.desc())
        .limit(1)
    )
    project = result.scalar_one_or_none()
    if project:
        return project_docs.project_response(project)

    project = Project(
        user_id=user_id,
        type=ProjectType.MEMOIR.value,
        title=DEFAULT_MEMOIR_PROJECT_TITLE,
    )
    db.add(project)
    await db.flush()
    await initialize_project_template(db, project)
    project = await load_project(db, project.id, user_id)
    return project_docs.project_response(project)


async def create_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    req: MemoirProjectCreate,
) -> ProjectResponse:
    title = (req.title or "").strip() or DEFAULT_MEMOIR_PROJECT_TITLE
    project = Project(
        user_id=user_id,
        type=ProjectType.MEMOIR.value,
        title=title,
        description=req.description,
    )
    db.add(project)
    await db.flush()
    await initialize_project_template(db, project)
    return await _load_memoir_project_response(db, project.id, user_id)


async def update_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    req: MemoirProjectUpdate,
) -> ProjectResponse:
    project = await load_project(db, project_id, user_id)
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")
    if req.title is not None:
        title = req.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="回忆录名称不能为空")
        project.title = title
    if req.description is not None:
        project.description = req.description
    project.updated_at = _utcnow()
    await db.commit()
    return await _load_memoir_project_response(db, project.id, user_id)


async def activate_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> MemoirProjectListResponse:
    project = await load_project(db, project_id, user_id)
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")
    project.updated_at = _utcnow()
    await db.commit()
    return await mobile_project_list(db, user_id)
