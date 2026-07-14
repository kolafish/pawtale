from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerKind,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskStatus,
    MemoirFollowupTaskUtteranceRole,
    MemoirNarrativeGapStatus,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirFollowupTask,
    MemoirFollowupTaskUtterance,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
    MemoirNarrativeGap,
)
from app.schemas.memoir_narrative import (
    MemoirClaimConfirmationResolution,
    MemoirClaimRejectionResolution,
    MemoirEntityMergeResolution,
    MemoirFactResolutionPlan,
)
from app.services.memoir import automation_event_resolution
from app.services.memoir.narrative import fact_resolution, gap_audit


class HistoricalFactAnswerRepairMismatch(ValueError):
    pass


_ENTITY_TYPE_LABELS = {
    "person": "人物",
    "place": "地点",
    "organization": "组织",
    "period": "时期",
}


@dataclass(frozen=True)
class HistoricalFactAnswerCandidate:
    project_id: str
    relation_id: str
    task_id: str
    utterance_id: str
    chapter_id: str | None
    gap_id: str
    interpretation_status: str
    task_status: str
    gap_status: str
    fact_text_sha256: str
    fact_text_length: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class HistoricalFactAnswerRepairSpec:
    project_id: str
    relation_id: str
    task_id: str
    utterance_id: str
    expected_chapter_id: str
    expected_gap_id: str
    expected_fact_text_sha256: str
    expected_interpretation_status: str
    expected_task_status: str
    expected_gap_status: str
    resolution_plan: MemoirFactResolutionPlan


@dataclass(frozen=True)
class HistoricalFactAnswerRepairPlan:
    candidate: HistoricalFactAnswerCandidate
    question: str
    resolution_reason: str
    operation_summaries: tuple[str, ...]
    operations: tuple[dict[str, object], ...]
    decision_ids: tuple[str, ...] = ()
    chapter_audit_job_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HistoricalFactAnswerRepairMismatch(message)


def _fact_text_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _candidate(
    relation: MemoirFollowupTaskUtterance,
    task: MemoirFollowupTask,
    gap: MemoirNarrativeGap,
) -> HistoricalFactAnswerCandidate:
    fact_text = relation.fact_resolution_text.strip()
    return HistoricalFactAnswerCandidate(
        project_id=relation.project_id,
        relation_id=relation.id,
        task_id=task.id,
        utterance_id=relation.utterance_id,
        chapter_id=task.chapter_id,
        gap_id=gap.id,
        interpretation_status=relation.interpretation_status,
        task_status=task.status,
        gap_status=gap.status,
        fact_text_sha256=_fact_text_hash(fact_text),
        fact_text_length=len(fact_text),
    )


def _missing_decision_condition():
    return ~select(MemoirNarrativeDecision.id).where(
        MemoirNarrativeDecision.project_id == MemoirFollowupTaskUtterance.project_id,
        MemoirNarrativeDecision.source_followup_task_utterance_id
        == MemoirFollowupTaskUtterance.id,
    ).exists()


async def audit_historical_fact_answers(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> list[HistoricalFactAnswerCandidate]:
    query = (
        select(MemoirFollowupTaskUtterance, MemoirFollowupTask, MemoirNarrativeGap)
        .join(
            MemoirFollowupTask,
            and_(
                MemoirFollowupTask.project_id == MemoirFollowupTaskUtterance.project_id,
                MemoirFollowupTask.id == MemoirFollowupTaskUtterance.task_id,
            ),
        )
        .join(
            MemoirNarrativeGap,
            and_(
                MemoirNarrativeGap.project_id == MemoirFollowupTask.project_id,
                MemoirNarrativeGap.id == MemoirFollowupTask.gap_id,
            ),
        )
        .where(
            MemoirFollowupTask.answer_use == MemoirFollowupAnswerUse.FACT_RESOLUTION.value,
            MemoirFollowupTaskUtterance.interpretation_status
            == MemoirFollowupAnswerInterpretationStatus.RESOLVED.value,
            MemoirFollowupTaskUtterance.answer_kind
            == MemoirFollowupAnswerKind.FACT_RESOLUTION.value,
            MemoirFollowupTask.status == MemoirFollowupTaskStatus.ANSWERED.value,
            MemoirNarrativeGap.status == MemoirNarrativeGapStatus.RESOLVED.value,
            func.length(func.trim(MemoirFollowupTaskUtterance.fact_resolution_text)) > 0,
            _missing_decision_condition(),
        )
        .order_by(MemoirFollowupTaskUtterance.created_at, MemoirFollowupTaskUtterance.id)
    )
    if project_id:
        query = query.where(MemoirFollowupTaskUtterance.project_id == project_id)
    return [
        _candidate(relation, task, gap)
        for relation, task, gap in (await db.execute(query)).all()
    ]


async def _load_repair_state(
    db: AsyncSession,
    *,
    spec: HistoricalFactAnswerRepairSpec,
) -> tuple[MemoirFollowupTaskUtterance, MemoirFollowupTask, MemoirNarrativeGap]:
    row = (
        await db.execute(
            select(MemoirFollowupTaskUtterance, MemoirFollowupTask, MemoirNarrativeGap)
            .options(selectinload(MemoirFollowupTask.claims))
            .join(
                MemoirFollowupTask,
                and_(
                    MemoirFollowupTask.project_id
                    == MemoirFollowupTaskUtterance.project_id,
                    MemoirFollowupTask.id == MemoirFollowupTaskUtterance.task_id,
                ),
            )
            .join(
                MemoirNarrativeGap,
                and_(
                    MemoirNarrativeGap.project_id == MemoirFollowupTask.project_id,
                    MemoirNarrativeGap.id == MemoirFollowupTask.gap_id,
                ),
            )
            .where(
                MemoirFollowupTaskUtterance.project_id == spec.project_id,
                MemoirFollowupTaskUtterance.id == spec.relation_id,
                MemoirFollowupTaskUtterance.task_id == spec.task_id,
                MemoirFollowupTaskUtterance.utterance_id == spec.utterance_id,
            )
        )
    ).one_or_none()
    _require(row is not None, "历史事实回答关系不存在或标识不一致")
    return row


async def _operation_summaries(
    db: AsyncSession,
    *,
    spec: HistoricalFactAnswerRepairSpec,
) -> tuple[str, ...]:
    entity_ids = {
        entity_id
        for operation in spec.resolution_plan.operations
        if isinstance(operation, MemoirEntityMergeResolution)
        for entity_id in (operation.source_entity_id, operation.retained_entity_id)
    }
    claim_ids = {
        operation.claim_id
        for operation in spec.resolution_plan.operations
        if isinstance(
            operation,
            (MemoirClaimConfirmationResolution, MemoirClaimRejectionResolution),
        )
    }
    entities = (
        {
            entity.id: entity
            for entity in (
                await db.scalars(
                    select(MemoirNarrativeEntity).where(
                        MemoirNarrativeEntity.project_id == spec.project_id,
                        MemoirNarrativeEntity.id.in_(entity_ids),
                    )
                )
            ).all()
        }
        if entity_ids
        else {}
    )
    claims = (
        {
            claim.id: claim
            for claim in (
                await db.scalars(
                    select(MemoirClaim).where(
                        MemoirClaim.project_id == spec.project_id,
                        MemoirClaim.id.in_(claim_ids),
                    )
                )
            ).all()
        }
        if claim_ids
        else {}
    )
    summaries: list[str] = []
    for operation in spec.resolution_plan.operations:
        if isinstance(operation, MemoirEntityMergeResolution):
            source = entities[operation.source_entity_id]
            retained = entities[operation.retained_entity_id]
            summaries.append(
                f"合并{_ENTITY_TYPE_LABELS.get(source.entity_type, '对象')}"
                f"“{source.canonical_name}”到“{retained.canonical_name}”"
            )
        elif isinstance(operation, MemoirClaimConfirmationResolution):
            summaries.append(f"确认事实“{claims[operation.claim_id].statement}”")
        else:
            summaries.append(f"否定事实“{claims[operation.claim_id].statement}”")
    return tuple(summaries)


async def inspect_historical_fact_answer_repair(
    db: AsyncSession,
    *,
    spec: HistoricalFactAnswerRepairSpec,
) -> HistoricalFactAnswerRepairPlan:
    _require(
        spec.expected_interpretation_status
        == MemoirFollowupAnswerInterpretationStatus.RESOLVED.value
        and spec.expected_task_status == MemoirFollowupTaskStatus.ANSWERED.value
        and spec.expected_gap_status == MemoirNarrativeGapStatus.RESOLVED.value,
        "历史修复仅允许处理已关闭但缺少语义决策的事实回答",
    )
    _require(
        len(spec.expected_fact_text_sha256) == 64
        and set(spec.expected_fact_text_sha256) <= set("0123456789abcdef"),
        "预期回答哈希必须是 SHA-256",
    )
    relation, task, gap = await _load_repair_state(db, spec=spec)
    candidate = _candidate(relation, task, gap)
    _require(relation.is_assignment_snapshot, "历史事实回答缺少任务快照")
    _require(
        relation.result_role == MemoirFollowupTaskUtteranceRole.TARGET_ANSWER.value,
        "历史事实回答尚未被标记为目标回答",
    )
    _require(
        relation.interpretation_status == spec.expected_interpretation_status,
        "历史事实回答解释状态与预期不一致",
    )
    _require(
        relation.answer_kind == MemoirFollowupAnswerKind.FACT_RESOLUTION.value,
        "历史回答不是事实核对回答",
    )
    _require(
        task.answer_use == MemoirFollowupAnswerUse.FACT_RESOLUTION.value,
        "历史任务不是事实核对任务",
    )
    _require(task.chapter_id == spec.expected_chapter_id, "历史任务章节与预期不一致")
    _require(gap.id == spec.expected_gap_id, "历史缺口标识与预期不一致")
    _require(task.status == spec.expected_task_status, "历史任务状态与预期不一致")
    _require(gap.status == spec.expected_gap_status, "历史缺口状态与预期不一致")
    _require(
        candidate.fact_text_sha256 == spec.expected_fact_text_sha256,
        "历史事实回答内容与预期哈希不一致",
    )
    existing_decision = await db.scalar(
        select(MemoirNarrativeDecision.id).where(
            MemoirNarrativeDecision.project_id == spec.project_id,
            MemoirNarrativeDecision.source_followup_task_utterance_id == spec.relation_id,
        ).limit(1)
    )
    _require(existing_decision is None, "历史事实回答已经存在语义决策")
    await fact_resolution.validate_resolved_fact_plan(
        db,
        project_id=spec.project_id,
        task=task,
        relation=relation,
        plan=spec.resolution_plan,
    )
    return HistoricalFactAnswerRepairPlan(
        candidate=candidate,
        question=task.question,
        resolution_reason=spec.resolution_plan.reason,
        operation_summaries=await _operation_summaries(db, spec=spec),
        operations=tuple(
            operation.model_dump(mode="json")
            for operation in spec.resolution_plan.operations
        ),
    )


async def apply_historical_fact_answer_repair(
    db: AsyncSession,
    *,
    spec: HistoricalFactAnswerRepairSpec,
) -> HistoricalFactAnswerRepairPlan:
    inspected = await inspect_historical_fact_answer_repair(db, spec=spec)
    relation, task, _gap = await _load_repair_state(db, spec=spec)
    applied = await fact_resolution.apply_resolved_fact_plan(
        db,
        project_id=spec.project_id,
        task=task,
        relation=relation,
        plan=spec.resolution_plan,
    )
    _require(
        len(applied) == len(spec.resolution_plan.operations),
        "历史事实回答的语义决策未完整写入",
    )
    relation.interpretation_reason = f"历史修复：{spec.resolution_plan.reason}"
    db.add(relation)
    await automation_event_resolution.resolve_followup_source_review_events(
        db,
        project_id=spec.project_id,
        task_id=task.id,
        resolved_by_utterance_id=relation.utterance_id,
    )
    audit_job = None
    if task.chapter_id:
        audit_job = await gap_audit.enqueue_chapter_gap_audit(
            db,
            project_id=spec.project_id,
            chapter_id=task.chapter_id,
            include_semantic_audit=False,
            ai_model=None,
            source="historical_fact_answer_repair",
        )
    await db.flush()
    return replace(
        inspected,
        decision_ids=tuple(decision.id for decision in applied),
        chapter_audit_job_id=audit_job.id if audit_job else None,
    )
