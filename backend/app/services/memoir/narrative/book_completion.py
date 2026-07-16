from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.memoir_narrative.book_completion import (
    MemoirBookCompletionOverviewResponse,
)
from app.services.memoir.book_plan_sources import (
    included_book_chapters,
    load_plan_with_chapters,
)
from app.services.memoir.narrative import chapter_completion_summary
from app.services.memoir.narrative.gap_rules import load_chapter_audit_contexts


async def get_book_completion_overview(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookCompletionOverviewResponse:
    plan = await load_plan_with_chapters(db, project_id)
    chapters = included_book_chapters(plan) if plan else []
    if not chapters:
        return MemoirBookCompletionOverviewResponse(
            project_id=project_id,
            headline="先确定这本书准备写哪些章节",
            detail="有了章节后，这里会按章显示还值得补充的内容。",
            total_chapters=0,
            completed_chapters=0,
            chapters=[],
        )

    contexts = await load_chapter_audit_contexts(
        db,
        project_id=project_id,
        chapters=chapters,
    )
    read_models = await chapter_completion_summary.get_chapter_completion_read_models(
        db,
        project_id=project_id,
        chapters=chapters,
        contexts=contexts,
    )
    chapter_items = [read_models[chapter.id].summary for chapter in chapters]
    completed = sum(item.state == "complete" for item in chapter_items)
    return MemoirBookCompletionOverviewResponse(
        project_id=project_id,
        headline="先看全书，再选择要补的章节",
        detail=f"全书共 {len(chapter_items)} 章，其中 {completed} 章目前不需要再补。",
        total_chapters=len(chapter_items),
        completed_chapters=completed,
        chapters=chapter_items,
    )
