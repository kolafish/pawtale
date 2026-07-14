from __future__ import annotations

from collections import defaultdict

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskStatus,
    MemoirNarrativeDecisionAction,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirFollowupTask,
    MemoirFollowupTaskUtterance,
    MemoirNarrativeDecision,
)
from app.schemas.memoir_narrative import MemoirChapterWorkspaceAnswerUpdateResponse


_ACTIVE_FOLLOWUP_STATUSES = {
    MemoirFollowupTaskStatus.PENDING.value,
    MemoirFollowupTaskStatus.IN_PROGRESS.value,
    MemoirFollowupTaskStatus.DEFERRED.value,
}
_FACT_DECISION_ACTIONS = {
    MemoirNarrativeDecisionAction.MERGE.value,
    MemoirNarrativeDecisionAction.CONFIRM.value,
    MemoirNarrativeDecisionAction.REJECT.value,
}


def _merge_summary(decision: MemoirNarrativeDecision) -> str:
    before = decision.before_state_json if isinstance(decision.before_state_json, dict) else {}
    source = before.get("source") if isinstance(before.get("source"), dict) else {}
    retained = before.get("retained") if isinstance(before.get("retained"), dict) else {}
    source_name = str(source.get("canonical_name") or "").strip()
    retained_name = str(retained.get("canonical_name") or "").strip()
    if source_name and retained_name:
        return f"已确认“{source_name}”和“{retained_name}”指的是同一个对象。"
    return "已确认两个重复称呼指的是同一个对象。"


def _decision_summary(
    decision: MemoirNarrativeDecision,
    *,
    claims_by_id: dict[str, MemoirClaim],
) -> str:
    if decision.action == MemoirNarrativeDecisionAction.MERGE.value:
        return _merge_summary(decision)
    claim = claims_by_id.get(decision.target_id)
    if claim is None:
        return ""
    if decision.action == MemoirNarrativeDecisionAction.CONFIRM.value:
        return f"已确认：{claim.statement}"
    if decision.action == MemoirNarrativeDecisionAction.REJECT.value:
        return f"已更正：原先记录的“{claim.statement}”不成立。"
    return ""


async def _pending_updates(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[MemoirChapterWorkspaceAnswerUpdateResponse]:
    rows = (
        await db.execute(
            select(MemoirFollowupTaskUtterance, MemoirFollowupTask)
            .join(
                MemoirFollowupTask,
                and_(
                    MemoirFollowupTask.project_id
                    == MemoirFollowupTaskUtterance.project_id,
                    MemoirFollowupTask.id == MemoirFollowupTaskUtterance.task_id,
                ),
            )
            .where(
                MemoirFollowupTaskUtterance.project_id == project_id,
                MemoirFollowupTaskUtterance.is_assignment_snapshot.is_(True),
                MemoirFollowupTaskUtterance.interpretation_status
                == MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION.value,
                MemoirFollowupTaskUtterance.confirmation_question != "",
                MemoirFollowupTask.chapter_id == chapter_id,
                MemoirFollowupTask.answer_use == MemoirFollowupAnswerUse.FACT_RESOLUTION.value,
                MemoirFollowupTask.status.in_(_ACTIVE_FOLLOWUP_STATUSES),
            )
            .order_by(
                MemoirFollowupTaskUtterance.created_at.desc(),
                MemoirFollowupTaskUtterance.id.desc(),
            )
        )
    ).all()
    updates: list[MemoirChapterWorkspaceAnswerUpdateResponse] = []
    seen_task_ids: set[str] = set()
    for relation, task in rows:
        if task.id in seen_task_ids:
            continue
        seen_task_ids.add(task.id)
        updates.append(
            MemoirChapterWorkspaceAnswerUpdateResponse(
                id=relation.id,
                task_id=task.id,
                status="needs_confirmation",
                question=relation.confirmation_question,
                summaries=[],
                created_at=relation.created_at,
            )
        )
    return updates


async def _applied_updates(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[MemoirChapterWorkspaceAnswerUpdateResponse]:
    latest_relation_ids = list(
        (
            await db.scalars(
                select(MemoirNarrativeDecision.source_followup_task_utterance_id)
                .join(
                    MemoirFollowupTaskUtterance,
                    and_(
                        MemoirNarrativeDecision.project_id
                        == MemoirFollowupTaskUtterance.project_id,
                        MemoirNarrativeDecision.source_followup_task_utterance_id
                        == MemoirFollowupTaskUtterance.id,
                    ),
                )
                .join(
                    MemoirFollowupTask,
                    and_(
                        MemoirFollowupTask.project_id
                        == MemoirFollowupTaskUtterance.project_id,
                        MemoirFollowupTask.id == MemoirFollowupTaskUtterance.task_id,
                    ),
                )
                .where(
                    MemoirNarrativeDecision.project_id == project_id,
                    MemoirNarrativeDecision.action.in_(_FACT_DECISION_ACTIONS),
                    MemoirNarrativeDecision.source_followup_task_utterance_id.is_not(None),
                    MemoirFollowupTask.chapter_id == chapter_id,
                )
                .group_by(MemoirNarrativeDecision.source_followup_task_utterance_id)
                .order_by(
                    func.max(MemoirNarrativeDecision.created_at).desc(),
                    MemoirNarrativeDecision.source_followup_task_utterance_id.desc(),
                )
                .limit(5)
            )
        ).all()
    )
    if not latest_relation_ids:
        return []
    rows = (
        await db.execute(
            select(
                MemoirNarrativeDecision,
                MemoirFollowupTaskUtterance,
                MemoirFollowupTask,
            )
            .join(
                MemoirFollowupTaskUtterance,
                and_(
                    MemoirNarrativeDecision.project_id
                    == MemoirFollowupTaskUtterance.project_id,
                    MemoirNarrativeDecision.source_followup_task_utterance_id
                    == MemoirFollowupTaskUtterance.id,
                ),
            )
            .join(
                MemoirFollowupTask,
                and_(
                    MemoirFollowupTask.project_id
                    == MemoirFollowupTaskUtterance.project_id,
                    MemoirFollowupTask.id == MemoirFollowupTaskUtterance.task_id,
                ),
            )
            .where(
                MemoirNarrativeDecision.project_id == project_id,
                MemoirNarrativeDecision.action.in_(_FACT_DECISION_ACTIONS),
                MemoirNarrativeDecision.source_followup_task_utterance_id.in_(
                    latest_relation_ids
                ),
                MemoirFollowupTask.chapter_id == chapter_id,
            )
            .order_by(
                MemoirNarrativeDecision.created_at.desc(),
                MemoirNarrativeDecision.id.desc(),
            )
        )
    ).all()
    claim_ids = {
        decision.target_id
        for decision, _relation, _task in rows
        if decision.action
        in {
            MemoirNarrativeDecisionAction.CONFIRM.value,
            MemoirNarrativeDecisionAction.REJECT.value,
        }
    }
    claims_by_id = (
        {
            claim.id: claim
            for claim in (
                await db.scalars(
                    select(MemoirClaim).where(
                        MemoirClaim.project_id == project_id,
                        MemoirClaim.id.in_(claim_ids),
                    )
                )
            ).all()
        }
        if claim_ids
        else {}
    )
    relations_by_id: dict[str, MemoirFollowupTaskUtterance] = {}
    tasks_by_relation_id: dict[str, MemoirFollowupTask] = {}
    decisions_by_relation_id: dict[str, list[MemoirNarrativeDecision]] = defaultdict(list)
    for decision, relation, task in rows:
        relations_by_id[relation.id] = relation
        tasks_by_relation_id[relation.id] = task
        decisions_by_relation_id[relation.id].append(decision)

    action_order = {
        MemoirNarrativeDecisionAction.MERGE.value: 0,
        MemoirNarrativeDecisionAction.CONFIRM.value: 1,
        MemoirNarrativeDecisionAction.REJECT.value: 2,
    }
    updates: list[MemoirChapterWorkspaceAnswerUpdateResponse] = []
    for relation_id, decisions in decisions_by_relation_id.items():
        relation = relations_by_id[relation_id]
        task = tasks_by_relation_id[relation_id]
        summaries = [
            summary
            for decision in sorted(
                decisions,
                key=lambda item: (action_order.get(item.action, 99), item.created_at, item.id),
            )
            for summary in [_decision_summary(decision, claims_by_id=claims_by_id)]
            if summary
        ]
        if not summaries:
            continue
        updates.append(
            MemoirChapterWorkspaceAnswerUpdateResponse(
                id=relation.id,
                task_id=task.id,
                status="applied",
                question=task.question,
                answer_text=relation.fact_resolution_text,
                summaries=summaries,
                created_at=max(decision.created_at for decision in decisions),
            )
        )
    updates.sort(key=lambda item: item.created_at, reverse=True)
    return updates


async def chapter_answer_updates(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[MemoirChapterWorkspaceAnswerUpdateResponse]:
    return [
        *(await _pending_updates(db, project_id=project_id, chapter_id=chapter_id)),
        *(await _applied_updates(db, project_id=project_id, chapter_id=chapter_id)),
    ]
