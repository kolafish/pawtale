from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirEditingJob
from app.schemas.memoir_narrative.book_completion import (
    MemoirBookCompletionOverviewResponse,
)
from app.services.memoir.book_plan_sources import (
    included_book_chapters,
    load_plan_with_chapters,
)
from app.services.memoir.narrative import (
    chapter_completion_summary,
    completion_refresh,
)
from app.services.memoir.narrative.gap_rules import (
    load_chapter_audit_context,
    load_chapter_audit_contexts,
)


async def prepare_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    user_id: str,
) -> MemoirEditingJob:
    del user_id
    await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    return await completion_refresh.enqueue_chapter_completion_refresh(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        include_semantic_audit=True,
        ai_model=None,
        source="book_completion_prepare",
    )


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
            detail="有了章节后，可以逐章整理、通读并确认正文。",
            total_chapters=0,
            completed_chapters=0,
            chapters=[],
        )

    contexts = await load_chapter_audit_contexts(
        db,
        project_id=project_id,
        chapters=chapters,
    )
    summaries = await chapter_completion_summary.get_chapter_completion_summaries(
        db,
        project_id=project_id,
        chapters=chapters,
        contexts=contexts,
    )
    chapter_items = [summaries[chapter.id] for chapter in chapters]
    completed = sum(item.state == "confirmed" for item in chapter_items)
    return MemoirBookCompletionOverviewResponse(
        project_id=project_id,
        headline="逐章整理这本书",
        detail=(
            "每章只显示一个下一步：需要补充时先讲一件事，"
            "否则直接生成正文供您通读。"
        ),
        total_chapters=len(chapter_items),
        completed_chapters=completed,
        chapters=chapter_items,
    )
