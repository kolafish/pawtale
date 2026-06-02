from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.projects.templates import initialize_project_template
from app.domain.projects.types import ProjectType
from app.models.projects import DocumentGroup, Project
from app.schemas.projects import ProjectResponse
from app.services import project_documents as project_docs
from app.services.projects.access import load_project


DEFAULT_MEMOIR_PROJECT_TITLE = "我的回忆录"


async def get_or_create_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
) -> ProjectResponse:
    result = await db.execute(
        select(Project)
        .where(Project.user_id == user_id, Project.type == ProjectType.MEMOIR.value)
        .options(
            selectinload(Project.groups).selectinload(DocumentGroup.documents),
            selectinload(Project.references),
        )
        .order_by(Project.updated_at.desc())
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
