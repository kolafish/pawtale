from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirCompletionItemAction,
    MemoirCompletionItemStatus,
)
from app.domain.memoir.types import MemoirEditingJobStatus
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.schemas.memoir_narrative.book_completion import (
    MemoirBookCompletionAction,
    MemoirBookCompletionChapterResponse,
    MemoirBookCompletionOverviewResponse,
    MemoirBookCompletionResponse,
    MemoirBookCompletionState,
)
from app.services.memoir.book_plan_sources import included_book_chapters, load_plan_with_chapters
from app.services.memoir.narrative import completion_items, followup_jobs, gap_audit
from app.services.memoir.narrative.gap_rules import ChapterAuditContext, load_chapter_audit_context


_ACTIVE_JOB_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}
_FAILED_JOB_STATUSES = {
    MemoirEditingJobStatus.FAILED.value,
    MemoirEditingJobStatus.SKIPPED.value,
}
_INTERVIEW_GUIDANCE = "围绕这个方向讲您记得的内容，不必逐项作答。"


@dataclass(frozen=True)
class _ChapterPreparation:
    chapter: MemoirBookPlanChapter
    context: ChapterAuditContext
    audit_job: MemoirEditingJob | None


def _response(
    project_id: str,
    *,
    state: MemoirBookCompletionState,
    action: MemoirBookCompletionAction,
    headline: str,
    detail: str,
    task_id: str | None = None,
    chapter_id: str | None = None,
) -> MemoirBookCompletionResponse:
    return MemoirBookCompletionResponse(
        project_id=project_id,
        state=state,
        action=action,
        headline=headline,
        detail=detail,
        task_id=task_id,
        chapter_id=chapter_id,
    )


async def _job_by_dedupe_key(db: AsyncSession, dedupe_key: str) -> MemoirEditingJob | None:
    return await db.scalar(
        select(MemoirEditingJob)
        .where(MemoirEditingJob.dedupe_key == dedupe_key)
        .execution_options(populate_existing=True)
    )


async def _chapter_preparations(
    db: AsyncSession,
    *,
    project_id: str,
    chapters: list[MemoirBookPlanChapter],
) -> list[_ChapterPreparation]:
    preparations: list[_ChapterPreparation] = []
    for chapter in chapters:
        context = await load_chapter_audit_context(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
        if context.contract is None:
            continue
        preparations.append(
            _ChapterPreparation(
                chapter=chapter,
                context=context,
                audit_job=await gap_audit.current_chapter_gap_audit_job(
                    db,
                    project_id=project_id,
                    chapter_id=chapter.id,
                    content_signature=gap_audit.chapter_gap_audit_content_signature(context),
                    include_semantic_audit=True,
                ),
            )
        )
    return preparations


def _audit_problem(
    project_id: str,
    preparation: _ChapterPreparation,
) -> MemoirBookCompletionResponse | None:
    job = preparation.audit_job
    if job is not None and job.status in _FAILED_JOB_STATUSES:
        return _response(
            project_id,
            state="failed",
            action="retry",
            headline="整理下一题时没有完成",
            detail=f"可以重新整理“{preparation.chapter.title}”，已有录音和书稿不会受影响。",
            chapter_id=preparation.chapter.id,
        )
    if job is not None and job.status in _ACTIVE_JOB_STATUSES:
        return _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在整理下一处值得补充的内容",
            detail=f"正在查看“{preparation.chapter.title}”，整理好后会显示一个优先方向。",
            chapter_id=preparation.chapter.id,
        )
    if job is None:
        return _response(
            project_id,
            state="needs_preparation",
            action="prepare",
            headline="先整理下一处值得补充的内容",
            detail="系统会根据现有书稿找出一个最值得优先补充的方向。",
            chapter_id=preparation.chapter.id,
        )
    return None


async def _generation_jobs(
    db: AsyncSession,
    *,
    project_id: str,
    preparations: list[_ChapterPreparation],
) -> dict[str, MemoirEditingJob | None]:
    jobs: dict[str, MemoirEditingJob | None] = {}
    for item in preparations:
        signature = await followup_jobs.followup_generation_signature(
            db,
            project_id=project_id,
            chapter_id=item.chapter.id,
        )
        dedupe_key = followup_jobs.followup_generation_dedupe_key(
            project_id=project_id,
            chapter_id=item.chapter.id,
            signature=signature,
        )
        jobs[item.chapter.id] = await _job_by_dedupe_key(db, dedupe_key)
    return jobs


def _generation_problem(
    project_id: str,
    preparation: _ChapterPreparation,
    job: MemoirEditingJob | None,
) -> MemoirBookCompletionResponse | None:
    if job is not None and job.status in _FAILED_JOB_STATUSES:
        return _response(
            project_id,
            state="failed",
            action="retry",
            headline="下一题没有整理出来",
            detail=f"可以重新整理“{preparation.chapter.title}”，已有内容不会丢失。",
            chapter_id=preparation.chapter.id,
        )
    if job is not None and job.status in _ACTIVE_JOB_STATUSES:
        return _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在准备一个具体问题",
            detail="问题准备好后会自动继续，不需要反复点击。",
            chapter_id=preparation.chapter.id,
        )
    if job is None:
        return _response(
            project_id,
            state="needs_preparation",
            action="prepare",
            headline="还差一步就能开始",
            detail="正在把书稿中缺少的内容整理成一个具体问题。",
            chapter_id=preparation.chapter.id,
        )
    return None


def _chapter_response_from_problem(
    preparation: _ChapterPreparation,
    problem: MemoirBookCompletionResponse,
) -> MemoirBookCompletionChapterResponse:
    return MemoirBookCompletionChapterResponse(
        chapter_id=preparation.chapter.id,
        title=preparation.chapter.title,
        sort_order=preparation.chapter.sort_order,
        state=problem.state,
        headline=problem.headline,
        detail=problem.detail,
    )


async def _chapter_completion_status(
    db: AsyncSession,
    *,
    project_id: str,
    chapter: MemoirBookPlanChapter,
    preparation: _ChapterPreparation | None,
    generation_job: MemoirEditingJob | None,
) -> MemoirBookCompletionChapterResponse:
    if preparation is None:
        return MemoirBookCompletionChapterResponse(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="needs_review",
            headline="先确认本章主要写什么",
            detail="确认本章方向后，系统才能判断最值得补充的内容。",
        )

    items = await completion_items.chapter_completion_items(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    interview_items = [
        item for item in items if item.action == MemoirCompletionItemAction.INTERVIEW
    ]
    in_progress = next(
        (
            item
            for item in interview_items
            if item.status == MemoirCompletionItemStatus.IN_PROGRESS
        ),
        None,
    )
    if in_progress is not None:
        return MemoirBookCompletionChapterResponse(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="in_progress",
            headline="这一章有一个方向正在补充",
            detail="可以回到刚才的语音访谈继续讲。",
            focus_areas=[in_progress.question],
            task_id=in_progress.task_id,
            active_session_id=in_progress.active_session_id,
        )

    audit_problem = _audit_problem(project_id, preparation)
    if audit_problem:
        return _chapter_response_from_problem(preparation, audit_problem)
    generation_problem = _generation_problem(project_id, preparation, generation_job)
    if generation_problem:
        return _chapter_response_from_problem(preparation, generation_problem)

    if interview_items:
        item = interview_items[0]
        deferred = item.status == MemoirCompletionItemStatus.DEFERRED
        return MemoirBookCompletionChapterResponse(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="ready",
            headline=("有一个补充方向留到以后" if deferred else "还有一个重要方向值得补充"),
            detail=(
                "准备好后，可以从这里继续。"
                if deferred
                else _INTERVIEW_GUIDANCE
            ),
            focus_areas=[item.question],
            task_id=item.task_id,
        )

    if any(item.requires_manual_review for item in items):
        return MemoirBookCompletionChapterResponse(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="needs_review",
            headline="有一处内容需要您确认",
            detail="这不是靠继续录音能解决的问题，请到本章查看。",
        )

    editorial_item = next(
        (
            item
            for item in items
            if item.action == MemoirCompletionItemAction.EDIT
        ),
        None,
    )
    if editorial_item is not None:
        return MemoirBookCompletionChapterResponse(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="needs_review",
            headline="已有素材，可以直接重新整理",
            detail="这处问题不需要继续采访，请到本章调整结构或正文。",
            focus_areas=[editorial_item.title],
        )

    return MemoirBookCompletionChapterResponse(
        chapter_id=chapter.id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        state="complete",
        headline="目前不需要再补",
        detail="以后新增素材或调整本章后，系统会重新整理。",
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
            detail="有了章节后，这里会按章显示还值得补充的内容。",
            total_chapters=0,
            completed_chapters=0,
            chapters=[],
        )

    preparations = await _chapter_preparations(
        db,
        project_id=project_id,
        chapters=chapters,
    )
    preparation_by_chapter = {item.chapter.id: item for item in preparations}
    generation_jobs = await _generation_jobs(
        db,
        project_id=project_id,
        preparations=preparations,
    )
    chapter_items = [
        await _chapter_completion_status(
            db,
            project_id=project_id,
            chapter=chapter,
            preparation=preparation_by_chapter.get(chapter.id),
            generation_job=generation_jobs.get(chapter.id),
        )
        for chapter in chapters
    ]
    completed = sum(item.state == "complete" for item in chapter_items)
    return MemoirBookCompletionOverviewResponse(
        project_id=project_id,
        headline="先看全书，再选择要补的章节",
        detail=f"全书共 {len(chapter_items)} 章，其中 {completed} 章目前不需要再补。",
        total_chapters=len(chapter_items),
        completed_chapters=completed,
        chapters=chapter_items,
    )


async def prepare_book_completion_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookCompletionResponse:
    plan = await load_plan_with_chapters(db, project_id)
    chapters = included_book_chapters(plan) if plan else []
    chapter = next((item for item in chapters if item.id == chapter_id), None)
    if chapter is None:
        raise ValueError("chapter does not belong to the project")
    preparations = await _chapter_preparations(
        db,
        project_id=project_id,
        chapters=[chapter],
    )
    if not preparations:
        return _response(
            project_id,
            state="needs_review",
            action="open_chapter",
            headline=f"先确定“{chapter.title}”主要写什么",
            detail="确认本章方向后，系统才能判断最值得补充的内容。",
            chapter_id=chapter.id,
        )
    preparation = preparations[0]
    items = await completion_items.chapter_completion_items(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    in_progress = next(
        (
            item
            for item in items
            if item.action == MemoirCompletionItemAction.INTERVIEW
            and item.status == MemoirCompletionItemStatus.IN_PROGRESS
        ),
        None,
    )
    if in_progress is not None:
        return _response(
            project_id,
            state="in_progress",
            action="start_interview",
            headline=in_progress.question,
            detail=_INTERVIEW_GUIDANCE,
            task_id=in_progress.task_id,
            chapter_id=chapter.id,
        )

    audit_job = preparation.audit_job
    if audit_job is None or audit_job.status in _FAILED_JOB_STATUSES:
        await gap_audit.enqueue_chapter_gap_audit(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
            include_semantic_audit=True,
            ai_model=None,
            source="book_completion",
        )
        return _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在查看本章还缺什么",
            detail="整理完成后，这里会显示最值得优先补充的方向。",
            chapter_id=chapter.id,
        )
    if audit_job.status in _ACTIVE_JOB_STATUSES:
        return _audit_problem(project_id, preparation) or _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在查看本章还缺什么",
            detail="整理完成后，这里会自动更新。",
            chapter_id=chapter.id,
        )

    generation_job = (await _generation_jobs(
        db,
        project_id=project_id,
        preparations=[preparation],
    ))[chapter.id]
    if generation_job is None or generation_job.status in _FAILED_JOB_STATUSES:
        await followup_jobs.schedule_followup_task_generation(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
            source="book_completion",
        )
        return _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在准备本章最值得补充的内容",
            detail="整理完成后，这里会自动更新。",
            chapter_id=chapter.id,
        )
    if generation_job.status in _ACTIVE_JOB_STATUSES:
        return _generation_problem(project_id, preparation, generation_job) or _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在准备本章最值得补充的内容",
            detail="整理完成后，这里会自动更新。",
            chapter_id=chapter.id,
        )

    items = await completion_items.chapter_completion_items(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    item = next(
        (
            item
            for item in items
            if item.action == MemoirCompletionItemAction.INTERVIEW
            and item.status
            in {
                MemoirCompletionItemStatus.OPEN,
                MemoirCompletionItemStatus.DEFERRED,
            }
        ),
        None,
    )
    if item is not None:
        return _response(
            project_id,
            state="ready",
            action="start_interview",
            headline=item.question,
            detail=_INTERVIEW_GUIDANCE,
            task_id=item.task_id,
            chapter_id=chapter.id,
        )

    manual_item = next(
        (
            item
            for item in items
            if item.action == MemoirCompletionItemAction.REVIEW
        ),
        None,
    )
    if manual_item is not None:
        return _response(
            project_id,
            state="needs_review",
            action="open_chapter",
            headline=manual_item.title,
            detail="这不是靠继续录音能解决的问题，请到本章确认。",
            chapter_id=chapter.id,
        )

    editorial_item = next(
        (
            item
            for item in items
            if item.action == MemoirCompletionItemAction.EDIT
        ),
        None,
    )
    if editorial_item is not None:
        return _response(
            project_id,
            state="needs_review",
            action="open_chapter",
            headline=editorial_item.title,
            detail="已有素材可以处理这处问题，请到本章调整结构或正文。",
            chapter_id=chapter.id,
        )

    return _response(
        project_id,
        state="complete",
        action="none",
        headline="这一章目前不需要再补",
        detail="以后新增素材或调整本章后，系统会重新整理。",
        chapter_id=chapter.id,
    )
