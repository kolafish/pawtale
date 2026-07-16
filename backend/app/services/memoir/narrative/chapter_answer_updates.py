from __future__ import annotations

from collections import defaultdict

from sqlalchemy import and_, select
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
    before = (
        decision.before_state_json
        if isinstance(decision.before_state_json, dict)
        else {}
    )
    source = before.get("source") if isinstance(before.get("source"), dict) else {}
    retained = (
        before.get("retained") if isinstance(before.get("retained"), dict) else {}
    )
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


async def chapter_answer_updates_by_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: list[str],
) -> dict[str, list[MemoirChapterWorkspaceAnswerUpdateResponse]]:
    updates_by_chapter: dict[
        str, list[MemoirChapterWorkspaceAnswerUpdateResponse]
    ] = {chapter_id: [] for chapter_id in chapter_ids}
    if not chapter_ids:
        return updates_by_chapter

    pending_rows = (
        await db.execute(
            select(MemoirFollowupTaskUtterance, MemoirFollowupTask)
            .join(
                MemoirFollowupTask,
                and_(
                    MemoirFollowupTask.project_id
                    == MemoirFollowupTaskUtterance.project_id,
                    MemoirFollowupTask.id
                    == MemoirFollowupTaskUtterance.task_id,
                ),
            )
            .where(
                MemoirFollowupTaskUtterance.project_id == project_id,
                MemoirFollowupTaskUtterance.is_assignment_snapshot.is_(True),
                MemoirFollowupTaskUtterance.interpretation_status
                == MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION.value,
                MemoirFollowupTaskUtterance.confirmation_question != "",
                MemoirFollowupTask.chapter_id.in_(chapter_ids),
                MemoirFollowupTask.answer_use
                == MemoirFollowupAnswerUse.FACT_RESOLUTION.value,
                MemoirFollowupTask.status.in_(_ACTIVE_FOLLOWUP_STATUSES),
            )
            .order_by(
                MemoirFollowupTask.chapter_id,
                MemoirFollowupTaskUtterance.created_at.desc(),
                MemoirFollowupTaskUtterance.id.desc(),
            )
        )
    ).all()
    seen_pending_task_ids: dict[str, set[str]] = defaultdict(set)
    for relation, task in pending_rows:
        chapter_id = task.chapter_id
        if chapter_id is None or task.id in seen_pending_task_ids[chapter_id]:
            continue
        seen_pending_task_ids[chapter_id].add(task.id)
        updates_by_chapter[chapter_id].append(
            MemoirChapterWorkspaceAnswerUpdateResponse(
                id=relation.id,
                task_id=task.id,
                status="needs_confirmation",
                question=relation.confirmation_question,
                summaries=[],
                created_at=relation.created_at,
            )
        )

    decision_rows = (
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
                    MemoirFollowupTask.id
                    == MemoirFollowupTaskUtterance.task_id,
                ),
            )
            .where(
                MemoirNarrativeDecision.project_id == project_id,
                MemoirNarrativeDecision.action.in_(_FACT_DECISION_ACTIONS),
                MemoirNarrativeDecision.source_followup_task_utterance_id.is_not(
                    None
                ),
                MemoirFollowupTask.chapter_id.in_(chapter_ids),
            )
            .order_by(
                MemoirFollowupTask.chapter_id,
                MemoirNarrativeDecision.created_at.desc(),
                MemoirNarrativeDecision.id.desc(),
            )
        )
    ).all()
    grouped_rows: dict[
        tuple[str, str],
        list[
            tuple[
                MemoirNarrativeDecision,
                MemoirFollowupTaskUtterance,
                MemoirFollowupTask,
            ]
        ],
    ] = defaultdict(list)
    for decision, relation, task in decision_rows:
        if task.chapter_id is not None:
            grouped_rows[(task.chapter_id, relation.id)].append(
                (decision, relation, task)
            )

    selected_group_keys: list[tuple[str, str]] = []
    keys_by_chapter: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key in grouped_rows:
        keys_by_chapter[key[0]].append(key)
    for chapter_id, keys in keys_by_chapter.items():
        keys.sort(
            key=lambda key: (
                max(row[0].created_at for row in grouped_rows[key]),
                key[1],
            ),
            reverse=True,
        )
        selected_group_keys.extend(keys[:5])

    claim_ids = {
        decision.target_id
        for key in selected_group_keys
        for decision, _relation, _task in grouped_rows[key]
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
    action_order = {
        MemoirNarrativeDecisionAction.MERGE.value: 0,
        MemoirNarrativeDecisionAction.CONFIRM.value: 1,
        MemoirNarrativeDecisionAction.REJECT.value: 2,
    }
    for chapter_id, relation_id in selected_group_keys:
        rows = grouped_rows[(chapter_id, relation_id)]
        relation = rows[0][1]
        task = rows[0][2]
        decisions = [row[0] for row in rows]
        summaries = [
            summary
            for decision in sorted(
                decisions,
                key=lambda item: (
                    action_order.get(item.action, 99),
                    item.created_at,
                    item.id,
                ),
            )
            for summary in [
                _decision_summary(decision, claims_by_id=claims_by_id)
            ]
            if summary
        ]
        if not summaries:
            continue
        updates_by_chapter[chapter_id].append(
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

    for chapter_updates in updates_by_chapter.values():
        pending = [
            update
            for update in chapter_updates
            if update.status == "needs_confirmation"
        ]
        applied = sorted(
            (
                update
                for update in chapter_updates
                if update.status == "applied"
            ),
            key=lambda item: item.created_at,
            reverse=True,
        )
        chapter_updates[:] = [*pending, *applied]
    return updates_by_chapter


async def chapter_answer_updates(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[MemoirChapterWorkspaceAnswerUpdateResponse]:
    return (
        await chapter_answer_updates_by_chapter(
            db,
            project_id=project_id,
            chapter_ids=[chapter_id],
        )
    )[chapter_id]
