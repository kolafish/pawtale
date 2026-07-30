from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import Project
from app.services.memoir.narrative import frameworks


async def initialize_memoir_project(db: AsyncSession, project: Project) -> None:
    await frameworks.initialize_project_framework(db, project_id=project.id)
    await db.commit()
