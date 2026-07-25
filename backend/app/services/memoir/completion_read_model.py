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
from app.services.memoir import (
    book_source_links,
    chapter_digests,
    chapter_generation,
    completion_issues,
    sections,
)


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
    pending_assignment_count = await sections.pending_assignment_material_count(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    if pending_assignment_count:
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="needs_input",
            next_action=MemoirChapterCompletionNextAction(
                kind="review_sections",
                title=f"还有 {pending_assignment_count} 条讲述待选择小节",
                detail="AI 没有强行归类，请逐条选择最合适的小节。",
                action_text="去选择",
            ),
            headline="请先整理待归类素材",
            detail="全部选择完成后，才能按新的小节结构生成正文。",
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
    digest = await chapter_digests.get_current_chapter_digest(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    digest_ready = bool(
        digest
        and digest.status == MemoirSemanticCacheStatus.READY.value
    )
    review_ready = bool(
        digest_ready
        or (
            draft_current
            and version is not None
            and version.prompt_snapshot
            == chapter_generation.CHAPTER_GENERATION_ENGINE
        )
    )
    if not review_ready:
        if (
            digest is not None
            and digest.status == MemoirSemanticCacheStatus.FAILED.value
        ):
            return MemoirChapterCompletionSummary(
                chapter_id=chapter.id,
                title=chapter.title,
                sort_order=chapter.sort_order,
                state="failed",
                next_action=MemoirChapterCompletionNextAction(
                    kind="prepare",
                    title="上次检查没有完成",
                    detail="原始素材没有丢失，可以重新检查。",
                    action_text="重新检查",
                ),
                headline="上次检查没有完成",
                detail="原始素材没有丢失。",
            )
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="not_started",
            next_action=MemoirChapterCompletionNextAction(
                kind="prepare",
                title="检查本章是否需要补充",
                detail="系统会根据当前素材判断本章最需要处理的一步。",
                action_text="检查本章",
            ),
            headline="尚未检查是否需要补充",
            detail="检查后只会给出一个明确的下一步。",
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
                action_text="通读并确认",
            ),
            headline="正文可以通读",
            detail="现有素材已经整理成完整章节。",
        )
    return MemoirChapterCompletionSummary(
        chapter_id=chapter.id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        state="not_started",
        next_action=MemoirChapterCompletionNextAction(
            kind="prepare",
            title="根据检查结果整理正文",
            detail="当前检查已经完成，正文需要按现有素材更新。",
            action_text="整理正文",
        ),
        headline="正文需要更新" if section_exists else "素材尚未归入小章节",
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
    needs_input = sum(item.state == "needs_input" for item in items)
    needs_check = sum(
        item.state in {"not_started", "failed"} for item in items
    )
    ready_to_review = sum(item.state == "draft_ready" for item in items)
    unfinished = len(items) - completed
    if unfinished:
        progress = []
        if needs_check:
            progress.append(f"{needs_check} 章待检查")
        if needs_input:
            progress.append(f"{needs_input} 章待补充或确认")
        if ready_to_review:
            progress.append(f"{ready_to_review} 章待通读")
        headline = f"还有 {unfinished} 章未完成"
        detail = "，".join(progress) + "。每章只显示当前最重要的一步。"
    else:
        headline = "全书正文已经确认"
        detail = "需要时仍可按当前素材重新检查。"
    return MemoirBookCompletionOverviewResponse(
        project_id=project_id,
        headline=headline,
        detail=detail,
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
    source_changes = await book_source_links.chapter_source_change_state(
        db,
        chapter=chapter,
    )
    signature_changed = False
    if version is not None:
        try:
            context = await chapter_generation.load_chapter_generation_context(
                db,
                project_id=project_id,
                chapter_id=chapter_id,
            )
            signature_changed = (
                bool(version.input_signature)
                and version.input_signature != context.input_signature
            )
        except HTTPException:
            signature_changed = False
    draft_needs_rewrite = bool(
        version is not None
        and (
            signature_changed
            or not version.section_snapshot_json
            or source_changes.pending_source_count
            or source_changes.removed_source_count
        )
    )
    active_issues = await completion_issues.list_issues(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    has_manual_review = any(
        issue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        for issue in active_issues
    )
    sections_by_chapter = await sections.section_summaries_by_chapter(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    chapter_sections = sections_by_chapter.get(chapter_id, [])
    pending_assignment_count = sum(
        section.material_count
        for section in chapter_sections
        if section.requires_user_assignment
    )
    return MemoirChapterWorkspaceResponse(
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_title=chapter.title,
        completion_summary=summary,
        draft_needs_rewrite=draft_needs_rewrite,
        can_rewrite_draft=(
            draft_needs_rewrite
            and not has_manual_review
            and not pending_assignment_count
        ),
        pending_assignment_material_count=pending_assignment_count,
        pending_completion_source_count=(
            source_changes.pending_completion_source_count
        ),
        pending_new_source_count=source_changes.pending_new_source_count,
        sections=chapter_sections,
        draft_id=draft.id if draft else None,
        draft_version_id=version.id if version else None,
        draft_version=version.version if version else None,
        draft_status=draft.status if draft else None,
        draft_content=version.content if version else "",
        draft_sections=(
            list(version.section_snapshot_json) if version else []
        ),
    )
