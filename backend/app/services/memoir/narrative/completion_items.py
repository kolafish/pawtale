from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirCompletionItemAction,
    MemoirCompletionItemKind,
    MemoirCompletionItemStatus,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskStatus,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapStatus,
    MemoirNarrativeGapType,
)
from app.models.memoir_narrative import MemoirFollowupTask, MemoirNarrativeGap
from app.schemas.memoir_narrative import MemoirCompletionItem


_ACTIVE_TASK_STATUSES = {
    MemoirFollowupTaskStatus.PENDING.value,
    MemoirFollowupTaskStatus.IN_PROGRESS.value,
    MemoirFollowupTaskStatus.DEFERRED.value,
}
_TASK_STATUS = {
    MemoirFollowupTaskStatus.PENDING.value: MemoirCompletionItemStatus.OPEN,
    MemoirFollowupTaskStatus.IN_PROGRESS.value: MemoirCompletionItemStatus.IN_PROGRESS,
    MemoirFollowupTaskStatus.DEFERRED.value: MemoirCompletionItemStatus.DEFERRED,
}
_SEVERITY_PRIORITY = {
    MemoirNarrativeGapSeverity.HIGH.value: 3000,
    MemoirNarrativeGapSeverity.MEDIUM.value: 2000,
    MemoirNarrativeGapSeverity.LOW.value: 1000,
}
MAX_VISIBLE_INTERVIEW_ITEMS_PER_CHAPTER = 3


def _task_kind(task: MemoirFollowupTask) -> MemoirCompletionItemKind:
    if task.answer_use == MemoirFollowupAnswerUse.FACT_RESOLUTION.value:
        return MemoirCompletionItemKind.FACT_CONFIRMATION
    return MemoirCompletionItemKind.NARRATOR_FOLLOWUP


def _gap_kind(gap: MemoirNarrativeGap) -> MemoirCompletionItemKind:
    if gap.gap_type == MemoirNarrativeGapType.MISSING_SLOT.value:
        return MemoirCompletionItemKind.OPTIONAL_ENRICHMENT
    if gap.gap_type in {
        MemoirNarrativeGapType.CONTRADICTION.value,
        MemoirNarrativeGapType.UNSUPPORTED_FACT.value,
    }:
        return MemoirCompletionItemKind.FACT_CONFIRMATION
    return MemoirCompletionItemKind.EDITORIAL_REVIEW


def _gap_action(gap: MemoirNarrativeGap) -> MemoirCompletionItemAction:
    if (
        gap.recommended_action
        == MemoirNarrativeGapRecommendedAction.INTERVIEW.value
    ):
        return MemoirCompletionItemAction.INTERVIEW
    if (
        gap.recommended_action
        == MemoirNarrativeGapRecommendedAction.EDITORIAL.value
    ):
        return MemoirCompletionItemAction.EDIT
    return MemoirCompletionItemAction.REVIEW


def _story_id(gap: MemoirNarrativeGap) -> str | None:
    if gap.primary_story_id:
        return gap.primary_story_id
    primary = next(
        (relation.story_id for relation in gap.stories if relation.role == "primary"),
        None,
    )
    return primary or next((relation.story_id for relation in gap.stories), None)


async def chapter_completion_items(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[MemoirCompletionItem]:
    all_tasks = list(
        (
            await db.scalars(
                select(MemoirFollowupTask)
                .join(
                    MemoirNarrativeGap,
                    MemoirNarrativeGap.id == MemoirFollowupTask.gap_id,
                )
                .where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.chapter_id == chapter_id,
                    MemoirFollowupTask.status.in_(_ACTIVE_TASK_STATUSES),
                    MemoirNarrativeGap.project_id == project_id,
                    MemoirNarrativeGap.status
                    == MemoirNarrativeGapStatus.OPEN.value,
                    MemoirNarrativeGap.recommended_action
                    == MemoirNarrativeGapRecommendedAction.INTERVIEW.value,
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
    tasks = all_tasks[:MAX_VISIBLE_INTERVIEW_ITEMS_PER_CHAPTER]
    task_by_gap = {task.gap_id: task for task in all_tasks}
    gap_filters = [
        MemoirNarrativeGap.project_id == project_id,
        or_(
            MemoirNarrativeGap.primary_chapter_id == chapter_id,
            MemoirNarrativeGap.chapters.any(chapter_id=chapter_id),
        ),
    ]
    if task_by_gap:
        gap_filters.append(
            or_(
                MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
                MemoirNarrativeGap.id.in_(task_by_gap),
            )
        )
    else:
        gap_filters.append(
            MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value
        )
    gaps = list(
        (
            await db.scalars(
                select(MemoirNarrativeGap)
                .options(
                    selectinload(MemoirNarrativeGap.stories),
                    selectinload(MemoirNarrativeGap.chapters),
                )
                .where(*gap_filters)
                .order_by(MemoirNarrativeGap.created_at, MemoirNarrativeGap.id)
            )
        ).unique().all()
    )
    gap_by_id = {gap.id: gap for gap in gaps}
    items: list[MemoirCompletionItem] = []
    for task in tasks:
        gap = gap_by_id.get(task.gap_id)
        if gap is None:
            continue
        items.append(
            MemoirCompletionItem(
                id=gap.id,
                project_id=project_id,
                chapter_id=chapter_id,
                story_id=task.story_id or _story_id(gap),
                gap_id=gap.id,
                task_id=task.id,
                kind=_task_kind(task),
                status=_TASK_STATUS[task.status],
                action=MemoirCompletionItemAction.INTERVIEW,
                gap_type=gap.gap_type,
                title=gap.title,
                detail=task.purpose,
                question=task.question,
                priority=task.priority,
                active_session_id=task.assigned_session_id,
            )
        )
    for gap in gaps:
        if gap.id in task_by_gap or gap.status != MemoirNarrativeGapStatus.OPEN.value:
            continue
        action = _gap_action(gap)
        if action == MemoirCompletionItemAction.INTERVIEW:
            continue
        items.append(
            MemoirCompletionItem(
                id=gap.id,
                project_id=project_id,
                chapter_id=chapter_id,
                story_id=_story_id(gap),
                gap_id=gap.id,
                kind=_gap_kind(gap),
                status=MemoirCompletionItemStatus.OPEN,
                action=action,
                gap_type=gap.gap_type,
                title=gap.title,
                detail=gap.description,
                priority=_SEVERITY_PRIORITY.get(gap.severity, 0),
                requires_manual_review=action == MemoirCompletionItemAction.REVIEW,
            )
        )
    status_rank = {
        MemoirCompletionItemStatus.IN_PROGRESS: 0,
        MemoirCompletionItemStatus.OPEN: 1,
        MemoirCompletionItemStatus.DEFERRED: 2,
        MemoirCompletionItemStatus.RESOLVED: 3,
        MemoirCompletionItemStatus.DISMISSED: 4,
    }
    return sorted(
        items,
        key=lambda item: (
            status_rank[item.status],
            -item.priority,
            item.id,
        ),
    )
