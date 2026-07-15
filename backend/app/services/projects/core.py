from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import is_admin_user
from app.domain.projects.types import ProjectType
from app.domain.projects.templates import initialize_project_template
from app.models.projects import DocumentGroup, Project
from app.models.users import User
from app.schemas.projects import ProjectCreate, ProjectListItem, ProjectResponse, ProjectUpdate
import app.services.project_documents as project_docs
from app.services.memoir import project_deletion as memoir_project_deletion
from app.services.projects.access import load_project


def _project_owner_fields(owner: User | None) -> dict[str, str | None]:
    return {
        "owner_user_id": owner.id if owner else None,
        "owner_email": owner.email if owner else None,
        "owner_display_name": owner.display_name if owner else None,
    }


def _can_edit_memoir_materials(project: Project, current_user: User, *, current_user_is_admin: bool) -> bool:
    return project.user_id == current_user.id or (
        current_user_is_admin and project.type == ProjectType.MEMOIR.value
    )


async def list_projects(db: AsyncSession, current_user: User) -> list[ProjectListItem]:
    current_user_is_admin = is_admin_user(current_user)
    filters = []
    if not current_user_is_admin:
        filters.append(Project.user_id == current_user.id)
    result = await db.execute(
        select(Project, User)
        .join(User, Project.user_id == User.id, isouter=True)
        .where(*filters)
        .options(selectinload(Project.groups).selectinload(DocumentGroup.documents))
        .order_by(Project.updated_at.desc())
    )
    rows = result.all()
    return [
        ProjectListItem(
            id=p.id,
            type=p.type,
            title=p.title,
            description=p.description,
            word_count_target=p.word_count_target,
            **_project_owner_fields(owner),
            can_edit=p.user_id == current_user.id,
            can_edit_memoir_materials=_can_edit_memoir_materials(
                p,
                current_user,
                current_user_is_admin=current_user_is_admin,
            ),
            metadata_json=p.metadata_json or {},
            total_word_count=project_docs.project_total_words(p),
            group_count=len(p.groups or []),
            document_count=sum(len(g.documents or []) for g in (p.groups or [])),
            updated_at=p.updated_at,
        )
        for p, owner in rows
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
    owner = await db.get(User, user_id)
    return project_docs.project_response(project, owner=owner, viewer_user_id=user_id)


async def get_project(db: AsyncSession, project_id: str, current_user: User) -> ProjectResponse:
    current_user_is_admin = is_admin_user(current_user)
    project = await load_project(
        db,
        project_id,
        current_user.id,
        allow_admin=current_user_is_admin,
    )
    owner = await db.get(User, project.user_id) if project.user_id else None
    return project_docs.project_response(
        project,
        owner=owner,
        viewer_user_id=current_user.id,
        viewer_is_admin=current_user_is_admin,
    )


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
    if project.type == ProjectType.MEMOIR.value:
        await memoir_project_deletion.delete_project(db, project)
        return
    await project_docs.delete_project(db, project)
