from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.article.template import initialize_article_project
from app.domain.memoir.template import initialize_memoir_project
from app.domain.novel.template import initialize_novel_project
from app.domain.projects.types import ProjectType
from app.models.projects import Project


PROJECT_TEMPLATE_INITIALIZERS = {
    ProjectType.NOVEL.value: initialize_novel_project,
    ProjectType.ARTICLE.value: initialize_article_project,
    ProjectType.MEMOIR.value: initialize_memoir_project,
}


async def initialize_project_template(db: AsyncSession, project: Project) -> None:
    initializer = PROJECT_TEMPLATE_INITIALIZERS.get(project.type)
    if initializer:
        await initializer(db, project)
        return

    await db.commit()
