from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.novel.template import initialize_novel_project
from app.domain.projects.types import ProjectType
from app.models.projects import Project


async def initialize_project_template(db: AsyncSession, project: Project) -> None:
    if project.type == ProjectType.NOVEL.value:
        await initialize_novel_project(db, project)
        return

    await db.commit()
