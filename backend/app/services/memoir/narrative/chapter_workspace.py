from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.memoir_narrative import MemoirChapterWorkspaceResponse
from app.services.memoir import completion_read_model


async def get_chapter_workspace(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterWorkspaceResponse:
    return await completion_read_model.chapter_workspace(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
