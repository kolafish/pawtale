from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueStatus,
    MemoirSemanticCacheStatus,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterDigest,
    MemoirCompletionIssue,
    MemoirSection,
    MemoirVoiceSession,
)
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirChapterCompletionIssue,
    MemoirChapterCompletionNextAction,
    MemoirChapterCompletionSummary,
    MemoirChapterWorkspaceResponse,
)
from app.services.memoir import chapter_generation, completion_issues


async def _plan_chapters(
    db: AsyncSession,
    *,
    project_id: str,
) -> list[MemoirBookPlanChapter]:
    plan = await db.scalar(
        select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id)
    )
    if plan is None:
        return []
    return list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter)
                .where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.plan_id == plan.id,
                    MemoirBookPlanChapter.include_in_book.is_(True),
                )
                .order_by(
                    MemoirBookPlanChapter.sort_order,
                    MemoirBookPlanChapter.id,
                )
            )
        ).all()
    )


async def _latest_version(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> tuple[MemoirBookChapterDraft | None, MemoirBookChapterDraftVersion | None]:
    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
    )
    version = await db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(
            MemoirBookChapterDraftVersion.project_id == project_id,
            MemoirBookChapterDraftVersion.chapter_id == chapter_id,
        )
        .order_by(
            MemoirBookChapterDraftVersion.version.desc(),
            MemoirBookChapterDraftVersion.created_at.desc(),
            MemoirBookChapterDraftVersion.id.desc(),
        )
        .limit(1)
    )
    return draft, version


async def _active_issue(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirCompletionIssue | None:
    issues = await completion_issues.list_issues(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    return next(
        (
            issue
            for issue in issues
            if issue.status != MemoirCompletionIssueStatus.CLOSED.value
        ),
        None,
    )


async def _active_issue_session_id(
    db: AsyncSession,
    *,
    project_id: str,
    issue_id: str,
) -> str | None:
    return await db.scalar(
        select(MemoirVoiceSession.id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceSession.current_completion_issue_id == issue_id,
            MemoirVoiceSession.status.not_in(
                {
                    MemoirVoiceSessionStatus.STOPPED.value,
                    MemoirVoiceSessionStatus.FAILED.value,
                }
            ),
        )
        .order_by(
            MemoirVoiceSession.updated_at.desc(),
            MemoirVoiceSession.id.desc(),
        )
        .limit(1)
    )


async def chapter_summary(
    db: AsyncSession,
    *,
    project_id: str,
    chapter: MemoirBookPlanChapter,
) -> MemoirChapterCompletionSummary:
    section_exists = (
        await db.scalar(
            select(MemoirSection.id)
            .where(
                MemoirSection.project_id == project_id,
                MemoirSection.chapter_id == chapter.id,
            )
            .limit(1)
        )
        is not None
    )
    issue = await _active_issue(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    draft, version = await _latest_version(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    current_signature = ""
    if section_exists:
        try:
            context = await chapter_generation.load_chapter_generation_context(
                db,
                project_id=project_id,
                chapter_id=chapter.id,
            )
            current_signature = context.input_signature
        except HTTPException:
            current_signature = ""
    draft_current = bool(
        version
        and version.input_signature
        and version.input_signature == current_signature
    )
    if (
        draft is not None
        and version is not None
        and draft.status == MemoirBookChapterDraftStatus.CONFIRMED.value
        and draft_current
    ):
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="confirmed",
            next_action=MemoirChapterCompletionNextAction(
                kind="complete",
                title="本章已确认",
                detail="正文已经收录进书稿，仍可继续修改。",
                action_text="查看正文",
            ),
            headline="本章已确认",
            detail="正文已经收录进书稿。",
        )
    if issue is not None:
        manual = (
            issue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        )
        item_status = (
            "in_progress"
            if issue.status == MemoirCompletionIssueStatus.IN_PROGRESS.value
            else "pending"
        )
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="needs_input",
            next_action=MemoirChapterCompletionNextAction(
                kind="confirm" if manual else "start_interview",
                title="需要您确认一件事" if manual else "建议补讲一件事",
                detail=issue.question,
                action_text="确认" if manual else "去补充",
                issue_id=issue.id,
            ),
            active_issue=MemoirChapterCompletionIssue(
                id=issue.id,
                question=issue.question,
                status=item_status,
                active_session_id=await _active_issue_session_id(
                    db,
                    project_id=project_id,
                    issue_id=issue.id,
                ),
            ),
            headline="需要确认" if manual else "还需要补充一点",
            detail=issue.reason,
        )
    if draft is not None and version is not None and draft_current:
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="draft_ready",
            next_action=MemoirChapterCompletionNextAction(
                kind="review_draft",
                title="正文已经整理好",
                detail="请通读、修改并确认本章。",
                action_text="查看正文",
            ),
            headline="正文可以通读",
            detail="现有素材已经整理成完整章节。",
        )
    failed_digest = await db.scalar(
        select(MemoirChapterDigest.id)
        .where(
            MemoirChapterDigest.project_id == project_id,
            MemoirChapterDigest.chapter_id == chapter.id,
            MemoirChapterDigest.status
            == MemoirSemanticCacheStatus.FAILED.value,
        )
        .order_by(MemoirChapterDigest.updated_at.desc())
        .limit(1)
    )
    if failed_digest is not None:
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="failed",
            next_action=MemoirChapterCompletionNextAction(
                kind="prepare",
                title="上次整理没有完成",
                detail="原始素材没有丢失，可以重新整理。",
                action_text="重新整理",
            ),
            headline="上次整理没有完成",
            detail="原始素材没有丢失。",
        )
    return MemoirChapterCompletionSummary(
        chapter_id=chapter.id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        state="not_started",
        next_action=MemoirChapterCompletionNextAction(
            kind="prepare",
            title="开始整理本章",
            detail=(
                "系统会先判断是否真的需要补充；"
                "素材足够时直接生成完整正文。"
            ),
            action_text="开始整理",
        ),
        headline="等待整理" if section_exists else "素材尚未归入小章节",
        detail="点击后只给出一个明确的下一步。",
    )


async def book_overview(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookCompletionOverviewResponse:
    chapters = await _plan_chapters(db, project_id=project_id)
    if not chapters:
        return MemoirBookCompletionOverviewResponse(
            project_id=project_id,
            headline="先确定这本书准备写哪些章节",
            detail="有了章节后，可以逐章整理、通读并确认正文。",
            total_chapters=0,
            completed_chapters=0,
            chapters=[],
        )
    items = [
        await chapter_summary(
            db,
            project_id=project_id,
            chapter=chapter,
        )
        for chapter in chapters
    ]
    completed = sum(item.state == "confirmed" for item in items)
    return MemoirBookCompletionOverviewResponse(
        project_id=project_id,
        headline="逐章整理这本书",
        detail="每章只显示一个下一步，不再经过多层审计页面。",
        total_chapters=len(items),
        completed_chapters=completed,
        chapters=items,
    )


async def chapter_workspace(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterWorkspaceResponse:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    summary = await chapter_summary(
        db,
        project_id=project_id,
        chapter=chapter,
    )
    draft, version = await _latest_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    return MemoirChapterWorkspaceResponse(
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_title=chapter.title,
        completion_summary=summary,
        draft_id=draft.id if draft else None,
        draft_version_id=version.id if version else None,
        draft_version=version.version if version else None,
        draft_status=draft.status if draft else None,
        draft_content=version.content if version else "",
    )
