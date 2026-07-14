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
    MemoirBookCompletionResponse,
    MemoirBookCompletionState,
)
from app.services.memoir.book_plan_sources import load_plan_with_chapters, sorted_chapters
from app.services.memoir.narrative import followup_jobs, followup_planner, gap_audit
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
    audit_dedupe_key: str
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
) -> tuple[list[_ChapterPreparation], MemoirBookPlanChapter | None]:
    preparations: list[_ChapterPreparation] = []
    first_needing_direction = None
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
            first_needing_direction = first_needing_direction or chapter
            continue
        signature = gap_audit.chapter_gap_audit_signature(
            context,
            include_semantic_audit=False,
            ai_model=None,
        )
        dedupe_key = gap_audit.chapter_gap_audit_dedupe_key(
            project_id=project_id,
            chapter_id=chapter.id,
            signature=signature,
        )
        preparations.append(
            _ChapterPreparation(
                chapter=chapter,
                context=context,
                audit_dedupe_key=dedupe_key,
                audit_job=await _job_by_dedupe_key(db, dedupe_key),
            )
        )
    return preparations, first_needing_direction


def _job_problem(
    project_id: str,
    preparations: list[_ChapterPreparation],
) -> MemoirBookCompletionResponse | None:
    failed = next(
        (item for item in preparations if item.audit_job and item.audit_job.status in _FAILED_JOB_STATUSES),
        None,
    )
    if failed:
        return _response(
            project_id,
            state="failed",
            action="retry",
            headline="整理下一题时没有完成",
            detail=f"可以重新整理“{failed.chapter.title}”，已有录音和书稿不会受影响。",
            chapter_id=failed.chapter.id,
        )
    active = next(
        (item for item in preparations if item.audit_job and item.audit_job.status in _ACTIVE_JOB_STATUSES),
        None,
    )
    if active:
        return _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在整理下一件要补的事",
            detail=f"正在查看“{active.chapter.title}”，整理好后会直接显示一个问题。",
            chapter_id=active.chapter.id,
        )
    missing = next((item for item in preparations if item.audit_job is None), None)
    if missing:
        return _response(
            project_id,
            state="needs_preparation",
            action="prepare",
            headline="先整理下一件要补的事",
            detail="系统会根据现有书稿找出一个最值得补充的问题。",
            chapter_id=missing.chapter.id,
        )
    return None


async def _generation_problem(
    db: AsyncSession,
    *,
    project_id: str,
    preparations: list[_ChapterPreparation],
) -> MemoirBookCompletionResponse | None:
    jobs: list[tuple[_ChapterPreparation, MemoirEditingJob | None]] = []
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
        jobs.append((item, await _job_by_dedupe_key(db, dedupe_key)))

    failed = next((pair for pair in jobs if pair[1] and pair[1].status in _FAILED_JOB_STATUSES), None)
    if failed:
        return _response(
            project_id,
            state="failed",
            action="retry",
            headline="下一题没有整理出来",
            detail=f"可以重新整理“{failed[0].chapter.title}”，已有内容不会丢失。",
            chapter_id=failed[0].chapter.id,
        )
    active = next((pair for pair in jobs if pair[1] and pair[1].status in _ACTIVE_JOB_STATUSES), None)
    if active:
        return _response(
            project_id,
            state="preparing",
            action="wait",
            headline="正在准备一个具体问题",
            detail="问题准备好后会自动继续，不需要反复点击。",
            chapter_id=active[0].chapter.id,
        )
    missing = next((pair for pair in jobs if pair[1] is None), None)
    if missing:
        return _response(
            project_id,
            state="needs_preparation",
            action="prepare",
            headline="还差一步就能开始",
            detail="正在把书稿中缺少的内容整理成一个具体问题。",
            chapter_id=missing[0].chapter.id,
        )
    return None


async def _first_open_gap_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapters: list[MemoirBookPlanChapter],
) -> MemoirBookPlanChapter | None:
    deferred_task_exists = (
        select(MemoirFollowupTask.id)
        .where(
            MemoirFollowupTask.project_id == project_id,
            MemoirFollowupTask.gap_id == MemoirNarrativeGap.id,
            MemoirFollowupTask.status == MemoirFollowupTaskStatus.DEFERRED.value,
        )
        .exists()
    )
    for chapter in chapters:
        gap_id = await db.scalar(
            select(MemoirNarrativeGap.id)
            .where(
                MemoirNarrativeGap.project_id == project_id,
                MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
                MemoirNarrativeGap.gap_type != MemoirNarrativeGapType.REPETITION.value,
                ~deferred_task_exists,
                or_(
                    MemoirNarrativeGap.primary_chapter_id == chapter.id,
                    MemoirNarrativeGap.chapters.any(chapter_id=chapter.id),
                ),
            )
            .limit(1)
        )
        if gap_id:
            return chapter
    return None


async def get_book_completion_status(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookCompletionResponse:
    plan = await load_plan_with_chapters(db, project_id)
    chapters = sorted_chapters(plan) if plan else []
    if not chapters:
        return _response(
            project_id,
            state="needs_review",
            action="open_book",
            headline="先整理这本书准备写哪些章节",
            detail="有了章节后，系统才能判断下一件需要补充的事。",
        )

    preparations, first_needing_direction = await _chapter_preparations(
        db,
        project_id=project_id,
        chapters=chapters,
    )
    if first_needing_direction:
        return _response(
            project_id,
            state="needs_review",
            action="open_chapter",
            headline=f"先确定“{first_needing_direction.title}”主要写什么",
            detail="确认本章方向后，系统才能判断还缺哪件事。",
            chapter_id=first_needing_direction.id,
        )
    problem = _job_problem(project_id, preparations)
    if problem:
        return problem
    if preparations:
        problem = await _generation_problem(
            db,
            project_id=project_id,
            preparations=preparations,
        )
        if problem:
            return problem

    task = await followup_planner.select_next_task(
        db,
        project_id=project_id,
        release_deferred=False,
    )
    if task is not None:
        return _response(
            project_id,
            state="ready",
            action="start_interview",
            headline=task.question,
            detail="这次只补这一件事，记得多少就说多少。",
            task_id=task.id,
            chapter_id=task.chapter_id,
        )

    open_gap_chapter = await _first_open_gap_chapter(
        db,
        project_id=project_id,
        chapters=chapters,
    )
    if open_gap_chapter:
        return _response(
            project_id,
            state="needs_review",
            action="open_chapter",
            headline=f"“{open_gap_chapter.title}”还有一件事需要确认",
            detail="这件事不适合直接用语音补充，请先查看本章给出的下一步。",
            chapter_id=open_gap_chapter.id,
        )
    incomplete_structure = next(
        (
            item
            for item in preparations
            if not item.context.nodes
            or not item.context.links
            or item.context.contract.status != MemoirChapterContractStatus.CONFIRMED.value
        ),
        None,
    )
    if incomplete_structure:
        return _response(
            project_id,
            state="needs_review",
            action="open_chapter",
            headline=f"确认“{incomplete_structure.chapter.title}”主要写什么",
            detail="本章方向和内容顺序确认后，才算完成当前整理。",
            chapter_id=incomplete_structure.chapter.id,
        )

    deferred_exists = await db.scalar(
        select(MemoirFollowupTask.id)
        .where(
            MemoirFollowupTask.project_id == project_id,
            MemoirFollowupTask.status == MemoirFollowupTaskStatus.DEFERRED.value,
        )
        .limit(1)
    )
    return _response(
        project_id,
        state="complete",
        action="none",
        headline="现在没有需要回答的问题",
        detail=(
            "之前选择稍后再说的问题仍然保留，可以以后继续。"
            if deferred_exists
            else "以后新增素材或调整章节后，系统会再整理下一题。"
        ),
    )


async def prepare_book_completion(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookCompletionResponse:
    current = await get_book_completion_status(db, project_id=project_id)
    if current.state in {"ready", "preparing"}:
        return current
    if current.state == "complete":
        task = await followup_planner.select_next_task(db, project_id=project_id)
        if task is None:
            return current
        return await get_book_completion_status(db, project_id=project_id)

    plan = await load_plan_with_chapters(db, project_id)
    chapters = sorted_chapters(plan) if plan else []
    if not chapters:
        return current
    preparations, _ = await _chapter_preparations(
        db,
        project_id=project_id,
        chapters=chapters,
    )

    audit_work_pending = False
    for item in preparations:
        job = item.audit_job
        if job is None or job.status in _FAILED_JOB_STATUSES:
            await gap_audit.enqueue_chapter_gap_audit(
                db,
                project_id=project_id,
                chapter_id=item.chapter.id,
                include_semantic_audit=False,
                ai_model=None,
                source="book_completion",
            )
            audit_work_pending = True
        elif job.status in _ACTIVE_JOB_STATUSES:
            audit_work_pending = True
    if audit_work_pending:
        return await get_book_completion_status(db, project_id=project_id)

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
        job = await _job_by_dedupe_key(db, dedupe_key)
        if job is None or job.status in _FAILED_JOB_STATUSES:
            await followup_jobs.schedule_followup_task_generation(
                db,
                project_id=project_id,
                chapter_id=item.chapter.id,
                source="book_completion",
            )
    return await get_book_completion_status(db, project_id=project_id)
