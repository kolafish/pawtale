from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirFollowupTaskStatus,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapStatus,
)
from app.domain.memoir.types import MemoirEditingJobStatus
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.models.memoir_narrative import MemoirDraftBlock, MemoirFollowupTask
from app.schemas.memoir_narrative import (
    MemoirChapterCompletionGap,
    MemoirChapterCompletionNextAction,
    MemoirChapterCompletionSummary,
    MemoirChapterCompletionTask,
    MemoirChapterWorkspaceAnswerUpdateResponse,
    MemoirChapterWorkspaceGapGroups,
)
from app.services.memoir.narrative import (
    chapter_answer_updates,
    followup_jobs,
    gap_audit,
    gaps,
)
from app.services.memoir.narrative.gap_rules import ChapterAuditContext
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
_INTERVIEW_GUIDANCE = "围绕这个方向讲您记得的内容，不必逐项作答。"
MAX_VISIBLE_TASKS = 3


@dataclass(frozen=True)
class ChapterCompletionReadModel:
    summary: MemoirChapterCompletionSummary
    gap_groups: MemoirChapterWorkspaceGapGroups
    answer_updates: list[MemoirChapterWorkspaceAnswerUpdateResponse]
    audit_job: MemoirEditingJob | None
    latest_audit_job: MemoirEditingJob | None
    generation_job: MemoirEditingJob | None


def _task_response(task: MemoirFollowupTask) -> MemoirChapterCompletionTask:
    return MemoirChapterCompletionTask(
        id=task.id,
        gap_id=task.gap_id,
        question=task.question,
        purpose=task.purpose,
        known_context_summary=task.known_context_summary,
        priority=task.priority,
        status=task.status,
        active_session_id=task.assigned_session_id,
    )


def _gap_response(gap) -> MemoirChapterCompletionGap:
    return MemoirChapterCompletionGap(
        id=gap.id,
        title=gap.title,
        description=gap.description,
        recommended_action=gap.recommended_action,
    )


def _next_action(
    kind: str,
    eyebrow: str,
    title: str,
    detail: str,
    *,
    action_text: str = "",
    disabled: bool = False,
    task_id: str | None = None,
) -> MemoirChapterCompletionNextAction:
    return MemoirChapterCompletionNextAction(
        kind=kind,
        eyebrow=eyebrow,
        title=title,
        detail=detail,
        action_text=action_text,
        disabled=disabled,
        task_id=task_id,
    )


async def _current_generation_job(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirEditingJob | None:
    signature = await followup_jobs.followup_generation_signature(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    dedupe_key = followup_jobs.followup_generation_dedupe_key(
        project_id=project_id,
        chapter_id=chapter_id,
        signature=signature,
    )
    return await db.scalar(
        select(MemoirEditingJob)
        .where(MemoirEditingJob.dedupe_key == dedupe_key)
        .execution_options(populate_existing=True)
    )


async def _active_tasks(
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
                    MemoirFollowupTask.status.in_(_ACTIVE_TASK_STATUSES),
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


async def _draft_blocks(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[MemoirDraftBlock]:
    return list(
        (
            await db.scalars(
                select(MemoirDraftBlock)
                .where(
                    MemoirDraftBlock.project_id == project_id,
                    MemoirDraftBlock.chapter_id == chapter_id,
                    MemoirDraftBlock.editorial_status
                    != MemoirDraftBlockEditorialStatus.EXCLUDED.value,
                )
                .order_by(MemoirDraftBlock.sort_order, MemoirDraftBlock.id)
            )
        ).all()
    )


def _draft_readiness(
    *,
    contract_is_confirmed: bool,
    audit_job: MemoirEditingJob | None,
    generation_job: MemoirEditingJob | None,
    draft_blocks: list[MemoirDraftBlock],
) -> str:
    if (
        not contract_is_confirmed
        or audit_job is None
        or audit_job.status != MemoirEditingJobStatus.SUCCEEDED.value
        or generation_job is None
        or generation_job.status != MemoirEditingJobStatus.SUCCEEDED.value
    ):
        return "blocked"
    if not draft_blocks:
        return "ready_to_generate"
    if any(
        block.editorial_status == MemoirDraftBlockEditorialStatus.DRAFT.value
        or block.freshness_status == MemoirDraftBlockFreshnessStatus.STALE.value
        for block in draft_blocks
    ):
        return "needs_review"
    return "ready"


def _summary(
    *,
    chapter: MemoirBookPlanChapter,
    context: ChapterAuditContext,
    gap_groups: MemoirChapterWorkspaceGapGroups,
    answer_updates: list[MemoirChapterWorkspaceAnswerUpdateResponse],
    active_tasks: list[MemoirFollowupTask],
    draft_blocks: list[MemoirDraftBlock],
    audit_job: MemoirEditingJob | None,
    generation_job: MemoirEditingJob | None,
) -> MemoirChapterCompletionSummary:
    contract = context.contract
    contract_is_confirmed = (
        contract is not None
        and contract.status == MemoirChapterContractStatus.CONFIRMED.value
    )
    open_gaps = {
        gap.id: gap
        for gap in [*gap_groups.must_handle, *gap_groups.suggested]
    }
    tasks_by_id = {task.id: task for task in active_tasks}
    confirmation_task_ids = {
        update.task_id
        for update in answer_updates
        if update.status == "needs_confirmation"
    }
    confirmation_gap_ids = {
        task.gap_id
        for task_id in confirmation_task_ids
        if (task := tasks_by_id.get(task_id)) is not None
    }
    interview_tasks = [
        task
        for task in active_tasks
        if task.id not in confirmation_task_ids
        and (gap := open_gaps.get(task.gap_id)) is not None
        and gap.recommended_action
        == MemoirNarrativeGapRecommendedAction.INTERVIEW
    ]
    visible_tasks: list[MemoirFollowupTask] = []
    visible_gap_ids: set[str] = set()
    for task in interview_tasks:
        if task.gap_id in visible_gap_ids:
            continue
        visible_gap_ids.add(task.gap_id)
        visible_tasks.append(task)
        if len(visible_tasks) == MAX_VISIBLE_TASKS:
            break
    in_progress_task = next(
        (
            task
            for task in visible_tasks
            if task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value
        ),
        None,
    )
    confirmation_update = next(
        (
            update
            for update in answer_updates
            if update.status == "needs_confirmation"
            and update.task_id in tasks_by_id
        ),
        None,
    )
    confirmation_task = (
        tasks_by_id.get(confirmation_update.task_id)
        if confirmation_update is not None
        else None
    )
    review_gaps = [
        gap
        for gap in open_gaps.values()
        if gap.recommended_action
        == MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW
        and gap.id not in confirmation_gap_ids
    ]
    editorial_gaps = [
        gap
        for gap in open_gaps.values()
        if gap.recommended_action
        == MemoirNarrativeGapRecommendedAction.EDITORIAL
    ]
    draft_readiness = _draft_readiness(
        contract_is_confirmed=contract_is_confirmed,
        audit_job=audit_job,
        generation_job=generation_job,
        draft_blocks=draft_blocks,
    )

    active_task: MemoirFollowupTask | None = None
    if contract is None:
        action = _next_action(
            "create_direction",
            "先定方向",
            "先确定这章主要写什么",
            "系统会根据现有素材先整理出本章主题和内容顺序，再请您确认。",
            action_text="整理本章方向",
        )
        state = "needs_review"
    elif not contract_is_confirmed:
        action = _next_action(
            "confirm_direction",
            "确认本章方向",
            contract.objective or "这个方向是否符合您的想法？",
            contract.reader_takeaway or "确认后就可以按这个方向继续整理。",
            action_text="这个方向可以",
        )
        state = "needs_review"
    elif confirmation_update is not None and confirmation_task is not None:
        active_task = confirmation_task
        action = _next_action(
            "continue_confirmation",
            "还需要您说明一句",
            confirmation_update.question,
            "刚才的回答还不能唯一对应到书里的人物或事实，直接再说明一句即可。",
            action_text="继续说明",
            task_id=confirmation_task.id,
        )
        state = "needs_review"
    elif in_progress_task is not None:
        active_task = in_progress_task
        action = _next_action(
            "start_interview",
            "继续刚才的讲述",
            in_progress_task.question,
            "这个方向正在补充，可以接着刚才的位置继续讲。",
            action_text="继续刚才的回答",
            task_id=in_progress_task.id,
        )
        state = "in_progress"
    elif audit_job is None:
        action = _next_action(
            "refresh_audit",
            "看看还缺什么",
            "整理这章还缺什么",
            "系统会查看现有内容，然后只给出一个最值得处理的下一步。",
            action_text="开始整理",
        )
        state = "needs_preparation"
    elif audit_job.status in _ACTIVE_JOB_STATUSES:
        action = _next_action(
            "wait",
            "正在整理",
            "正在查看本章还缺什么",
            "整理完成后，这里会自动显示下一步。",
            disabled=True,
        )
        state = "preparing"
    elif audit_job.status in _FAILED_JOB_STATUSES:
        action = _next_action(
            "refresh_audit",
            "需要重试",
            "上次没有整理完成",
            audit_job.error_message or "已有正文不会受影响，可以重新整理。",
            action_text="重新整理",
        )
        state = "failed"
    elif generation_job is None:
        action = _next_action(
            "refresh_audit",
            "准备补充方向",
            "还差一步就能看到本章下一步",
            "系统会把审计结果整理成可执行的补讲、编辑或确认方向。",
            action_text="继续整理",
        )
        state = "needs_preparation"
    elif generation_job.status in _ACTIVE_JOB_STATUSES:
        action = _next_action(
            "wait",
            "正在整理",
            "正在准备本章下一步",
            "整理完成后，这里会自动更新。",
            disabled=True,
        )
        state = "preparing"
    elif generation_job.status in _FAILED_JOB_STATUSES:
        action = _next_action(
            "refresh_audit",
            "需要重试",
            "本章下一步没有整理出来",
            generation_job.error_message or "已有录音和正文不会受影响，可以重新整理。",
            action_text="重新整理",
        )
        state = "failed"
    elif review_gaps:
        gap = review_gaps[0]
        action = _next_action(
            "open_manual_review",
            "先确认一件事",
            gap.title,
            gap.description,
            action_text="处理这件事",
        )
        state = "needs_review"
    elif editorial_gaps:
        gap = editorial_gaps[0]
        action = _next_action(
            "open_editorial_fix",
            "可以直接整理",
            gap.title,
            "相关素材已经存在，不需要继续采访，调整结构或正文即可。",
            action_text="查看并整理正文",
        )
        state = "needs_review"
    elif visible_tasks:
        active_task = visible_tasks[0]
        action = _next_action(
            "start_interview",
            "选择一个方向继续讲",
            active_task.question,
            _INTERVIEW_GUIDANCE,
            action_text=(
                "继续刚才的回答"
                if active_task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value
                else "从这个方向开始"
            ),
            task_id=active_task.id,
        )
        state = "ready"
    elif draft_readiness == "ready_to_generate":
        action = _next_action(
            "generate_draft",
            "开始写正文",
            "现有内容已经可以整理成这一章",
            "系统会按本章方向生成初稿，生成后再由您通读确认。",
            action_text="生成本章初稿",
        )
        state = "complete"
    elif draft_readiness == "needs_review":
        action = _next_action(
            "review_draft",
            "通读正文",
            "本章正文需要您看一遍",
            "只需关注内容是否准确、是否像讲述者本人，不必理解内部状态。",
            action_text="查看本章正文",
        )
        state = "complete"
    else:
        action = _next_action(
            "complete",
            "本章已整理",
            "这一章目前没有必须处理的内容",
            "以后增加新素材时，系统会重新给出下一步。",
            disabled=True,
        )
        state = "complete"

    priority_count = len(gap_groups.must_handle)
    if contract is None:
        headline = "先确定这章主要写什么"
        detail = "整理方向后，您会先看到整章还缺什么，再决定从哪里开始。"
    elif not contract_is_confirmed:
        headline = "本章方向等待确认"
        detail = "确认写作重点后，系统才会继续判断补讲、编辑和事实确认。"
    elif audit_job is not None and audit_job.status in _ACTIVE_JOB_STATUSES:
        headline = "正在整理本章的完整情况"
        detail = "整理完成后会自动显示可选择的补充方向。"
    elif confirmation_update is not None or review_gaps:
        count = len(
            {
                *confirmation_task_ids,
                *(gap.id for gap in review_gaps),
            }
        )
        headline = f"有 {count} 件事需要先确认"
        detail = "这些内容会影响人物、时间或事实是否准确，确认后再进入正文。"
    elif editorial_gaps:
        headline = f"有 {len(editorial_gaps)} 处可以直接重新整理"
        detail = "相关素材已经存在，不需要继续采访，调整结构或正文即可。"
    elif draft_readiness == "needs_review":
        headline = "新回答已收到，正文需要重新整理"
        detail = "重新整理只更新受影响的段落，您手动保护的内容不会被覆盖。"
    elif draft_blocks:
        headline = "现有内容可以形成章节正文"
        detail = (
            "正文已经可读；其余方向属于可选补充，不会阻止成书。"
            if gap_groups.suggested
            else "目前没有必须补充的内容，可以直接通读本章正文。"
        )
    elif priority_count:
        headline = f"有 {priority_count} 项需要优先处理"
        detail = "先处理会影响成书准确性或结构完整性的内容。"
    elif visible_tasks:
        headline = f"还有 {len(visible_tasks)} 个方向可以补充"
        detail = _INTERVIEW_GUIDANCE
    else:
        headline = "现有内容已经可以开始写正文"
        detail = "目前没有必须补充的内容，可以继续整理章节正文。"

    return MemoirChapterCompletionSummary(
        chapter_id=chapter.id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        state=state,
        audit_status=(audit_job.status if audit_job is not None else "not_started"),
        audit_is_current=audit_job is not None,
        next_action=action,
        must_handle_count=len(gap_groups.must_handle),
        suggested_count=len(gap_groups.suggested),
        accepted_incomplete_count=sum(
            gap.status == MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE
            for gap in gap_groups.accepted_incomplete
        ),
        active_task=(_task_response(active_task) if active_task is not None else None),
        visible_tasks=[_task_response(task) for task in visible_tasks],
        editorial_gaps=[_gap_response(gap) for gap in editorial_gaps],
        review_gaps=[_gap_response(gap) for gap in review_gaps],
        draft_readiness=draft_readiness,
        headline=headline,
        detail=detail,
    )


async def get_chapter_completion_read_model(
    db: AsyncSession,
    *,
    project_id: str,
    chapter: MemoirBookPlanChapter,
    context: ChapterAuditContext,
) -> ChapterCompletionReadModel:
    content_signature = gap_audit.chapter_gap_audit_content_signature(context)
    latest_audit_job = await gap_audit.latest_current_chapter_gap_audit_job(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
        content_signature=content_signature,
    )
    audit_job = None
    generation_job = None
    if context.contract is not None:
        audit_job = await gap_audit.current_chapter_gap_audit_job(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
            content_signature=content_signature,
            include_semantic_audit=True,
        )
        if (
            audit_job is not None
            and audit_job.status == MemoirEditingJobStatus.SUCCEEDED.value
        ):
            generation_job = await _current_generation_job(
                db,
                project_id=project_id,
                chapter_id=chapter.id,
            )

    chapter_gaps, _ = await gaps.list_gaps(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
        limit=500,
    )
    gap_responses = [await gaps.gap_response(db, gap=gap) for gap in chapter_gaps]
    gap_groups = MemoirChapterWorkspaceGapGroups(
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
        accepted_incomplete=[
            gap
            for gap in gap_responses
            if gap.status
            in {
                MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE,
                MemoirNarrativeGapStatus.NOT_APPLICABLE,
                MemoirNarrativeGapStatus.DISMISSED,
            }
        ],
    )
    answer_updates = await chapter_answer_updates.chapter_answer_updates(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    active_tasks = await _active_tasks(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    draft_blocks = await _draft_blocks(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    return ChapterCompletionReadModel(
        summary=_summary(
            chapter=chapter,
            context=context,
            gap_groups=gap_groups,
            answer_updates=answer_updates,
            active_tasks=active_tasks,
            draft_blocks=draft_blocks,
            audit_job=audit_job,
            generation_job=generation_job,
        ),
        gap_groups=gap_groups,
        answer_updates=answer_updates,
        audit_job=audit_job,
        latest_audit_job=latest_audit_job,
        generation_job=generation_job,
    )
