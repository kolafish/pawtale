from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.memoir_narrative import MemoirChapterWorkspaceResponse
from app.services.memoir.narrative import chapter_completion_summary
from app.services.memoir.narrative.gap_rules import load_chapter_audit_context


async def get_chapter_workspace(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterWorkspaceResponse:
    context = await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    completion_summary = await chapter_completion_summary.get_chapter_completion_summary(
        db,
        project_id=project_id,
        chapter=context.chapter,
        context=context,
    )
    return MemoirChapterWorkspaceResponse(
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_title=context.chapter.title,
        completion_summary=completion_summary,
        draft_id=context.draft.id if context.draft else None,
        draft_version_id=context.draft.version_id if context.draft else None,
        draft_version=context.draft.version if context.draft else None,
        draft_status=context.draft.status if context.draft else None,
        draft_content=context.draft.content if context.draft else "",
    )
