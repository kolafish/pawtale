from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirFollowupTaskStatus,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapStatus,
)
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlanChapter,
    MemoirEditingJob,
)
from app.models.memoir_narrative import (
    MemoirDraftBlock,
    MemoirFollowupTask,
    MemoirNarrativeGap,
    MemoirNarrativeGapChapter,
)
from app.schemas.memoir_narrative import (
    MemoirChapterCompletionConfirmation,
    MemoirChapterCompletionNextAction,
    MemoirChapterCompletionSummary,
    MemoirChapterCompletionTask,
    MemoirNarrativeGapResponse,
)
from app.services.memoir.narrative import chapter_answer_updates, completion_refresh
from app.services.memoir.narrative.gap_rules import (
    ChapterAuditContext,
    ChapterAuditDraftBlock,
)
from app.services.memoir.narrative.readiness_policy import gap_blocks_formalization


_ACTIVE_JOB_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}
_FAILED_JOB_STATUSES = {
    MemoirEditingJobStatus.FAILED.value,
    MemoirEditingJobStatus.SKIPPED.value,
}
_ACTIVE_TASK_STATUSES = {
    MemoirFollowupTaskStatus.PENDING.value,
    MemoirFollowupTaskStatus.IN_PROGRESS.value,
    MemoirFollowupTaskStatus.DEFERRED.value,
}


@dataclass(frozen=True)
class ChapterGapGroups:
    must_handle: list[MemoirNarrativeGapResponse]
    suggested: list[MemoirNarrativeGapResponse]


def _task_response(task: MemoirFollowupTask) -> MemoirChapterCompletionTask:
    return MemoirChapterCompletionTask(
        id=task.id,
        question=task.question,
        status=task.status,
        active_session_id=task.assigned_session_id,
    )


def _next_action(
    kind: str,
    title: str,
    detail: str,
    *,
    action_text: str = "",
    disabled: bool = False,
    task_id: str | None = None,
    confirmation_id: str | None = None,
) -> MemoirChapterCompletionNextAction:
    return MemoirChapterCompletionNextAction(
        kind=kind,
        title=title,
        detail=detail,
        action_text=action_text,
        disabled=disabled,
        task_id=task_id,
        confirmation_id=confirmation_id,
    )


def _current_draft_blocks(
    blocks: Sequence[MemoirDraftBlock | ChapterAuditDraftBlock],
) -> list[MemoirDraftBlock | ChapterAuditDraftBlock]:
    return [
        block
        for block in blocks
        if block.editorial_status != MemoirDraftBlockEditorialStatus.EXCLUDED.value
    ]


def _summary(
    *,
    chapter: MemoirBookPlanChapter,
    context: ChapterAuditContext,
    gap_groups: ChapterGapGroups,
    pending_confirmations: list[
        chapter_answer_updates.ChapterPendingAnswerConfirmation
    ],
    active_tasks: list[MemoirFollowupTask],
    refresh_job: MemoirEditingJob | None,
    draft_job: MemoirEditingJob | None,
    chapter_draft_status: str | None,
) -> MemoirChapterCompletionSummary:
    contract_is_confirmed = (
        context.contract is not None
        and context.contract.status == MemoirChapterContractStatus.CONFIRMED.value
    )
    open_gaps = {
        gap.id: gap
        for gap in [*gap_groups.must_handle, *gap_groups.suggested]
    }
    tasks_by_id = {task.id: task for task in active_tasks}
    confirmation_update = next(
        (
            update
            for update in pending_confirmations
            if update.task_id in tasks_by_id
        ),
        None,
    )
    confirmation_task = (
        tasks_by_id.get(confirmation_update.task_id)
        if confirmation_update is not None
        else None
    )
    confirmation_task_ids = {
        update.task_id
        for update in pending_confirmations
    }
    interview_tasks = [
        task
        for task in active_tasks
        if task.id not in confirmation_task_ids
        and (gap := open_gaps.get(task.gap_id)) is not None
        and gap.recommended_action
        == MemoirNarrativeGapRecommendedAction.INTERVIEW
    ]
    active_task = next(
        (
            task
            for status in (
                MemoirFollowupTaskStatus.IN_PROGRESS.value,
                MemoirFollowupTaskStatus.PENDING.value,
                MemoirFollowupTaskStatus.DEFERRED.value,
            )
            for task in interview_tasks
            if task.status == status
        ),
        None,
    )
    manual_gap = next(
        (
            gap
            for gap in open_gaps.values()
            if gap.recommended_action
            == MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW
        ),
        None,
    )
    blocks = _current_draft_blocks(context.draft_blocks)
    blocks_are_current = bool(blocks) and all(
        block.freshness_status == MemoirDraftBlockFreshnessStatus.CURRENT.value
        for block in blocks
    )

    confirmation: MemoirChapterCompletionConfirmation | None = None
    if not contract_is_confirmed:
        state = "not_started"
        headline = "可以开始整理"
        detail = "系统会根据现有讲述确定写作顺序，并判断是否真的还需要补充。"
        action = _next_action(
            "prepare",
            "整理这一章",
            detail,
            action_text="开始整理",
        )
    elif refresh_job is not None and refresh_job.status in _ACTIVE_JOB_STATUSES:
        state = "preparing"
        headline = "正在检查现有讲述"
        detail = "系统正在判断哪些内容可以直接写入正文。"
        action = _next_action("wait", headline, detail, disabled=True)
    elif refresh_job is not None and refresh_job.status in _FAILED_JOB_STATUSES:
        state = "failed"
        headline = "上次没有整理完成"
        detail = refresh_job.error_message or "已有录音和文字没有丢失，可以重新整理。"
        action = _next_action(
            "prepare",
            "重新整理这一章",
            detail,
            action_text="重新整理",
        )
    elif confirmation_update is not None and confirmation_task is not None:
        state = "needs_input"
        headline = "还需要说明一句"
        detail = "刚才的回答还不能准确对应到书里的人物或事实。"
        active_task = confirmation_task
        action = _next_action(
            "continue_confirmation",
            confirmation_update.question,
            detail,
            action_text="继续说明",
            task_id=confirmation_task.id,
        )
    elif active_task is not None:
        state = "needs_input"
        headline = "还需要讲一件事"
        detail = "讲完这一件，系统会自动继续整理正文。"
        action = _next_action(
            "start_interview",
            active_task.question,
            detail,
            action_text=(
                "继续讲"
                if active_task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value
                else "开始讲"
            ),
            task_id=active_task.id,
        )
    elif manual_gap is not None:
        state = "needs_input"
        headline = "还需要确认一件事"
        detail = "这会影响正文中的事实是否准确，确认后系统会继续整理。"
        confirmation = MemoirChapterCompletionConfirmation(
            id=manual_gap.id,
            question=manual_gap.title,
            detail=manual_gap.description,
        )
        action = _next_action(
            "confirm_fact",
            manual_gap.title,
            manual_gap.description,
            action_text="确认正确说法",
            confirmation_id=manual_gap.id,
        )
    elif draft_job is not None and draft_job.status in _ACTIVE_JOB_STATUSES:
        state = "preparing"
        headline = "正在整理正文"
        detail = "系统正在把已有讲述写成连贯正文，完成后可以直接通读。"
        action = _next_action(
            "wait",
            headline,
            detail,
            disabled=True,
        )
    elif blocks_are_current:
        if chapter_draft_status == MemoirBookChapterDraftStatus.CONFIRMED.value:
            state = "confirmed"
            headline = "本章已确认"
            detail = "正文已经收入书稿；以后有新讲述时还可以重新整理。"
            action = _next_action(
                "complete",
                headline,
                detail,
                disabled=True,
            )
        else:
            state = "draft_ready"
            headline = "正文已经整理好"
            detail = "请通读整章；需要时可以修改，确认后就会收入书稿。"
            action = _next_action(
                "review_draft",
                "通读本章正文",
                detail,
                action_text="查看正文",
            )
    elif draft_job is not None and draft_job.status in _FAILED_JOB_STATUSES:
        state = "failed"
        headline = "正文没有整理完成"
        detail = draft_job.error_message or "已有录音和文字没有丢失，可以重新整理。"
        action = _next_action(
            "prepare",
            "重新整理这一章",
            detail,
            action_text="重新整理",
        )
    elif refresh_job is None:
        state = "not_started"
        headline = "可以开始整理"
        detail = "系统会查看现有讲述；只有关键内容确实缺失时才会请您补充。"
        action = _next_action(
            "prepare",
            "整理这一章",
            detail,
            action_text="开始整理",
        )
    else:
        # This covers data created before automatic draft generation was added.
        state = "not_started"
        headline = "可以继续整理正文"
        detail = "现有内容已经检查过，再点一次就会按新流程自动生成正文。"
        action = _next_action(
            "prepare",
            "继续整理这一章",
            detail,
            action_text="继续整理",
        )

    return MemoirChapterCompletionSummary(
        chapter_id=chapter.id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        state=state,
        next_action=action,
        active_task=(_task_response(active_task) if active_task is not None else None),
        confirmation=confirmation,
        headline=headline,
        detail=detail,
    )


def _gap_groups(
    gap_responses: list[MemoirNarrativeGapResponse],
) -> ChapterGapGroups:
    return ChapterGapGroups(
        must_handle=[
            gap
            for gap in gap_responses
            if gap.status == MemoirNarrativeGapStatus.OPEN
            and gap_blocks_formalization(
                gap_type=gap.gap_type.value,
                severity=gap.severity.value,
            )
        ],
        suggested=[
            gap
            for gap in gap_responses
            if gap.status == MemoirNarrativeGapStatus.OPEN
            and not gap_blocks_formalization(
                gap_type=gap.gap_type.value,
                severity=gap.severity.value,
            )
        ],
    )


async def get_chapter_completion_summaries(
    db: AsyncSession,
    *,
    project_id: str,
    chapters: list[MemoirBookPlanChapter],
    contexts: dict[str, ChapterAuditContext],
) -> dict[str, MemoirChapterCompletionSummary]:
    chapter_ids = [chapter.id for chapter in chapters]
    if not chapter_ids:
        return {}
    if set(chapter_ids) != set(contexts):
        raise ValueError("completion contexts do not match chapters")

    jobs = list(
        (
            await db.scalars(
                select(MemoirEditingJob)
                .where(
                    MemoirEditingJob.project_id == project_id,
                    MemoirEditingJob.chapter_id.in_(chapter_ids),
                    MemoirEditingJob.job_type.in_(
                        {
                            MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value,
                            MemoirEditingJobType.GENERATE_DRAFT_BLOCKS.value,
                        }
                    ),
                )
                .order_by(
                    MemoirEditingJob.chapter_id,
                    MemoirEditingJob.updated_at.desc(),
                    MemoirEditingJob.created_at.desc(),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    jobs_by_chapter: dict[str, list[MemoirEditingJob]] = {
        chapter_id: [] for chapter_id in chapter_ids
    }
    for job in jobs:
        if job.chapter_id is not None:
            jobs_by_chapter[job.chapter_id].append(job)

    chapter_gap_ids = select(MemoirNarrativeGapChapter.gap_id).where(
        MemoirNarrativeGapChapter.project_id == project_id,
        MemoirNarrativeGapChapter.chapter_id.in_(chapter_ids),
    )
    chapter_gaps = list(
        (
            await db.scalars(
                select(MemoirNarrativeGap)
                .options(
                    selectinload(MemoirNarrativeGap.claims),
                    selectinload(MemoirNarrativeGap.stories),
                    selectinload(MemoirNarrativeGap.chapters),
                )
                .where(
                    MemoirNarrativeGap.project_id == project_id,
                    or_(
                        MemoirNarrativeGap.primary_chapter_id.in_(chapter_ids),
                        MemoirNarrativeGap.id.in_(chapter_gap_ids),
                    ),
                )
                .order_by(
                    MemoirNarrativeGap.status,
                    MemoirNarrativeGap.severity,
                    MemoirNarrativeGap.last_detected_at.desc(),
                )
            )
        ).unique().all()
    )
    gap_responses_by_chapter: dict[str, list[MemoirNarrativeGapResponse]] = {
        chapter_id: [] for chapter_id in chapter_ids
    }
    for gap in chapter_gaps:
        response = MemoirNarrativeGapResponse.model_validate(gap)
        related_chapter_ids = {relation.chapter_id for relation in gap.chapters}
        if gap.primary_chapter_id:
            related_chapter_ids.add(gap.primary_chapter_id)
        for related_chapter_id in related_chapter_ids:
            if related_chapter_id in gap_responses_by_chapter:
                gap_responses_by_chapter[related_chapter_id].append(response)

    active_tasks = list(
        (
            await db.scalars(
                select(MemoirFollowupTask)
                .where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.chapter_id.in_(chapter_ids),
                    MemoirFollowupTask.status.in_(_ACTIVE_TASK_STATUSES),
                )
                .order_by(
                    MemoirFollowupTask.chapter_id,
                    (
                        MemoirFollowupTask.status
                        == MemoirFollowupTaskStatus.IN_PROGRESS.value
                    ).desc(),
                    MemoirFollowupTask.priority.desc(),
                    MemoirFollowupTask.created_at,
                    MemoirFollowupTask.id,
                )
            )
        ).all()
    )
    tasks_by_chapter: dict[str, list[MemoirFollowupTask]] = {
        chapter_id: [] for chapter_id in chapter_ids
    }
    for task in active_tasks:
        if task.chapter_id is not None:
            tasks_by_chapter[task.chapter_id].append(task)

    draft_statuses = dict(
        (
            await db.execute(
                select(
                    MemoirBookChapterDraft.chapter_id,
                    MemoirBookChapterDraft.status,
                ).where(
                    MemoirBookChapterDraft.project_id == project_id,
                    MemoirBookChapterDraft.chapter_id.in_(chapter_ids),
                )
            )
        ).all()
    )
    pending_confirmations_by_chapter = (
        await chapter_answer_updates.pending_answer_confirmations_by_chapter(
            db,
            project_id=project_id,
            chapter_ids=chapter_ids,
        )
    )

    summaries: dict[str, MemoirChapterCompletionSummary] = {}
    for chapter in chapters:
        context = contexts[chapter.id]
        content_signature = completion_refresh.chapter_completion_content_signature(
            context
        )
        chapter_jobs = jobs_by_chapter[chapter.id]
        refresh_job = next(
            (
                job
                for job in chapter_jobs
                if job.job_type
                == MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value
                and isinstance(job.payload_json, dict)
                and job.payload_json.get("content_signature") == content_signature
            ),
            None,
        )
        draft_job = next(
            (
                job
                for job in chapter_jobs
                if job.job_type == MemoirEditingJobType.GENERATE_DRAFT_BLOCKS.value
            ),
            None,
        )
        gap_groups = _gap_groups(gap_responses_by_chapter[chapter.id])
        pending_confirmations = pending_confirmations_by_chapter[chapter.id]
        summaries[chapter.id] = _summary(
            chapter=chapter,
            context=context,
            gap_groups=gap_groups,
            pending_confirmations=pending_confirmations,
            active_tasks=tasks_by_chapter[chapter.id],
            refresh_job=refresh_job,
            draft_job=draft_job,
            chapter_draft_status=draft_statuses.get(chapter.id),
        )
    return summaries


async def get_chapter_completion_summary(
    db: AsyncSession,
    *,
    project_id: str,
    chapter: MemoirBookPlanChapter,
    context: ChapterAuditContext,
) -> MemoirChapterCompletionSummary:
    return (
        await get_chapter_completion_summaries(
            db,
            project_id=project_id,
            chapters=[chapter],
            contexts={chapter.id: context},
        )
    )[chapter.id]
