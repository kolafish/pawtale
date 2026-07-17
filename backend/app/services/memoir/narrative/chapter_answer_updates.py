from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupTaskStatus,
)
from app.models.memoir_narrative import (
    MemoirFollowupTask,
    MemoirFollowupTaskAnswer,
    MemoirUtteranceInterviewAssignment,
)


_ACTIVE_FOLLOWUP_STATUSES = {
    MemoirFollowupTaskStatus.PENDING.value,
    MemoirFollowupTaskStatus.IN_PROGRESS.value,
    MemoirFollowupTaskStatus.DEFERRED.value,
}


@dataclass(frozen=True)
class ChapterPendingAnswerConfirmation:
    id: str
    task_id: str
    question: str


async def pending_answer_confirmations_by_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: list[str],
) -> dict[str, list[ChapterPendingAnswerConfirmation]]:
    confirmations: dict[str, list[ChapterPendingAnswerConfirmation]] = {
        chapter_id: [] for chapter_id in chapter_ids
    }
    if not chapter_ids:
        return confirmations

    rows = (
        await db.execute(
            select(MemoirFollowupTaskAnswer, MemoirFollowupTask)
            .join(
                MemoirFollowupTask,
                and_(
                    MemoirFollowupTask.project_id
                    == MemoirFollowupTaskAnswer.project_id,
                    MemoirFollowupTask.id
                    == MemoirFollowupTaskAnswer.task_id,
                ),
            )
            .join(
                MemoirUtteranceInterviewAssignment,
                MemoirUtteranceInterviewAssignment.utterance_id
                == MemoirFollowupTaskAnswer.utterance_id,
            )
            .where(
                MemoirFollowupTaskAnswer.project_id == project_id,
                MemoirUtteranceInterviewAssignment.followup_task_id
                == MemoirFollowupTaskAnswer.task_id,
                MemoirFollowupTaskAnswer.interpretation_status
                == MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION.value,
                MemoirFollowupTaskAnswer.confirmation_question != "",
                MemoirFollowupTask.chapter_id.in_(chapter_ids),
                MemoirFollowupTask.status.in_(_ACTIVE_FOLLOWUP_STATUSES),
            )
            .order_by(
                MemoirFollowupTask.chapter_id,
                MemoirFollowupTaskAnswer.created_at.desc(),
                MemoirFollowupTaskAnswer.id.desc(),
            )
        )
    ).all()
    seen_task_ids: dict[str, set[str]] = defaultdict(set)
    for relation, task in rows:
        chapter_id = task.chapter_id
        if chapter_id is None or task.id in seen_task_ids[chapter_id]:
            continue
        seen_task_ids[chapter_id].add(task.id)
        confirmations[chapter_id].append(
            ChapterPendingAnswerConfirmation(
                id=relation.id,
                task_id=task.id,
                question=relation.confirmation_question,
            )
        )
    return confirmations
