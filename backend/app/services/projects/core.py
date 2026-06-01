from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.projects.templates import initialize_project_template
from app.models.projects import DocumentGroup, Project
from app.schemas.projects import ProjectCreate, ProjectListItem, ProjectResponse, ProjectUpdate
from app.services import project_documents as project_docs
from app.services.projects.access import load_project


async def list_projects(db: AsyncSession, user_id: str) -> list[ProjectListItem]:
    result = await db.execute(
        select(Project)
        .where(Project.user_id == user_id)
        .options(selectinload(Project.groups).selectinload(DocumentGroup.documents))
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()
    return [
        ProjectListItem(
            id=p.id,
            type=p.type,
            title=p.title,
            description=p.description,
            word_count_target=p.word_count_target,
            total_word_count=project_docs.project_total_words(p),
            group_count=len(p.groups or []),
            document_count=sum(len(g.documents or []) for g in (p.groups or [])),
            updated_at=p.updated_at,
        )
        for p in projects
    ]


async def create_project(db: AsyncSession, user_id: str, req: ProjectCreate) -> ProjectResponse:
    project = Project(
        user_id=user_id,
        type=req.type.value,
        title=req.title,
        description=req.description,
        word_count_target=req.word_count_target,
        daily_word_goal=req.daily_word_goal,
    )
    db.add(project)
    await db.flush()

    await initialize_project_template(db, project)
    project = await load_project(db, project.id, user_id)
    return project_docs.project_response(project)


async def get_project(db: AsyncSession, project_id: str, user_id: str) -> ProjectResponse:
    project = await load_project(db, project_id, user_id)
    return project_docs.project_response(project)


async def update_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: ProjectUpdate,
) -> ProjectResponse:
    project = await load_project(db, project_id, user_id)
    for field in ["title", "description", "word_count_target", "daily_word_goal"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(project, field, val)
    await db.commit()
    project = await load_project(db, project.id, user_id)
    return project_docs.project_response(project)


async def delete_project(db: AsyncSession, project_id: str, user_id: str) -> None:
    project = await load_project(db, project_id, user_id)
    await project_docs.delete_project(db, project)
