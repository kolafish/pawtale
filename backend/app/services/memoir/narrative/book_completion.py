from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirFollowupTaskStatus,
    MemoirNarrativeGapStatus,
    MemoirNarrativeGapType,
)
from app.domain.memoir.types import MemoirEditingJobStatus
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.models.memoir_narrative import MemoirFollowupTask, MemoirNarrativeGap
from app.schemas.memoir_narrative.book_completion import (
    MemoirBookCompletionAction,
    MemoirBookCompletionChapterResponse,
    MemoirBookCompletionOverviewResponse,
    MemoirBookCompletionResponse,
    MemoirBookCompletionState,
)
from app.services.memoir.book_plan_sources import load_plan_with_chapters, sorted_chapters
from app.services.memoir.narrative import followup_jobs, gap_audit
from app.services.memoir.narrative.gap_rules import ChapterAuditContext, load_chapter_audit_context


_ACTIVE_JOB_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}
_FAILED_JOB_STATUSES = {
    MemoirEditingJobStatus.FAILED.value,
    MemoirEditingJobStatus.SKIPPED.value,
}


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
        if (
            context.contract is None
            or context.contract.status != MemoirChapterContractStatus.CONFIRMED.value
        ):
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
            headline="正在整理下一件要补的事",
            detail=f"正在查看“{preparation.chapter.title}”，整理好后会直接显示一个问题。",
            chapter_id=preparation.chapter.id,
        )
    if job is None:
        return _response(
            project_id,
            state="needs_preparation",
            action="prepare",
            headline="先整理下一件要补的事",
            detail="系统会根据现有书稿找出一个最值得补充的问题。",
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


async def _chapter_active_tasks(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[MemoirFollowupTask]:
    return list(
        (
            await db.scalars(
                select(MemoirFollowupTask)
                .where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.chapter_id == chapter_id,
                    MemoirFollowupTask.status.in_(
                        [
                            MemoirFollowupTaskStatus.PENDING.value,
                            MemoirFollowupTaskStatus.IN_PROGRESS.value,
                            MemoirFollowupTaskStatus.DEFERRED.value,
                        ]
                    ),
                )
                .order_by(
                    (
                        MemoirFollowupTask.status
                        == MemoirFollowupTaskStatus.IN_PROGRESS.value
                    ).desc(),
                    (
                        MemoirFollowupTask.status
                        == MemoirFollowupTaskStatus.PENDING.value
                    ).desc(),
                    MemoirFollowupTask.priority.desc(),
                    MemoirFollowupTask.created_at,
                    MemoirFollowupTask.id,
                )
            )
        ).all()
    )


async def _chapter_has_manual_review_gap(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> bool:
    gap_id = await db.scalar(
        select(MemoirNarrativeGap.id)
        .where(
            MemoirNarrativeGap.project_id == project_id,
            MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
            MemoirNarrativeGap.gap_type.notin_(
                [
                    MemoirNarrativeGapType.MISSING_SLOT.value,
                    MemoirNarrativeGapType.REPETITION.value,
                ]
            ),
            or_(
                MemoirNarrativeGap.primary_chapter_id == chapter_id,
                MemoirNarrativeGap.chapters.any(chapter_id=chapter_id),
            ),
        )
        .limit(1)
    )
    return gap_id is not None


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

    tasks = await _chapter_active_tasks(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    in_progress = next(
        (task for task in tasks if task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value),
        None,
    )
    if in_progress is not None:
        return MemoirBookCompletionChapterResponse(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="in_progress",
            headline="这一章有一段正在补充",
            detail="可以回到刚才的语音访谈继续讲。",
            remaining_count=len(tasks),
            focus_areas=[task.question for task in tasks[:3]],
            task_id=in_progress.id,
            active_session_id=in_progress.assigned_session_id,
        )

    audit_problem = _audit_problem(project_id, preparation)
    if audit_problem:
        return _chapter_response_from_problem(preparation, audit_problem)
    generation_problem = _generation_problem(project_id, preparation, generation_job)
    if generation_problem:
        return _chapter_response_from_problem(preparation, generation_problem)

    if tasks:
        task = tasks[0]
        deferred = task.status == MemoirFollowupTaskStatus.DEFERRED.value
        return MemoirBookCompletionChapterResponse(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="ready",
            headline=("有一段经历留到以后再补" if deferred else "还有一段重要经历值得补充"),
            detail=(
                "准备好后，可以从这里继续。"
                if deferred
                else "先讲完整经过，不需要逐个回答细节。"
            ),
            remaining_count=len(tasks),
            focus_areas=[item.question for item in tasks[:3]],
            task_id=task.id,
        )

    if await _chapter_has_manual_review_gap(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    ):
        return MemoirBookCompletionChapterResponse(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="needs_review",
            headline="有一处内容需要您确认",
            detail="这不是靠继续录音能解决的问题，请到本章查看。",
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
    chapters = sorted_chapters(plan) if plan else []
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
    chapters = sorted_chapters(plan) if plan else []
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
    active_tasks = await _chapter_active_tasks(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    in_progress = next(
        (
            task
            for task in active_tasks
            if task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value
        ),
        None,
    )
    if in_progress is not None:
        return _response(
            project_id,
            state="in_progress",
            action="start_interview",
            headline=in_progress.question,
            detail="先讲完整经过，不需要逐个回答细节。",
            task_id=in_progress.id,
            chapter_id=chapter.id,
        )

    audit_job = preparation.audit_job
    if audit_job is None or audit_job.status in _FAILED_JOB_STATUSES:
        await gap_audit.enqueue_chapter_gap_audit(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
            include_semantic_audit=False,
            ai_model=None,
            source="book_completion",
        )
        return _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在查看本章还缺什么",
            detail="整理完成后，这里会显示最值得补充的一段经历。",
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

    active_tasks = await _chapter_active_tasks(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    task = next(
        (
            item
            for item in active_tasks
            if item.status
            in {
                MemoirFollowupTaskStatus.PENDING.value,
                MemoirFollowupTaskStatus.DEFERRED.value,
            }
        ),
        None,
    )
    if task is not None:
        return _response(
            project_id,
            state="ready",
            action="start_interview",
            headline=task.question,
            detail="先讲完整经过，不需要逐个回答细节。",
            task_id=task.id,
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
