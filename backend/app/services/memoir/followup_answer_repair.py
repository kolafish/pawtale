from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerKind,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskAnswerRole,
    MemoirNarrativeActorType,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirChapterSourceLinkStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirDraftBlockClaim,
    MemoirDraftBlockStory,
    MemoirFollowupTask,
    MemoirFollowupTaskClaim,
    MemoirFollowupTaskAnswer,
    MemoirNarrativeGap,
    MemoirNarrativeGapClaim,
    MemoirNarrativeGapStory,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStorySlot,
    MemoirStorySlotClaim,
)
from app.schemas.memoir_book import MemoirBookSourceRouteResponse
from app.services.memoir.book_plan_sources import (
    SOURCES_CHANGED_STATUS_REASON,
    draft_status_metadata,
    pending_source_ids,
    removed_source_ids,
    set_source_change_metadata,
)
from app.services.memoir.narrative import draft_blocks, interview_policy
from app.services.memoir.narrative.followup_completion import (
    complete_task_from_semantic_interpretation,
)
from app.services.memoir.source_disposition import apply_source_route_disposition


class FollowupAnswerRepairMismatch(ValueError):
    pass


T = TypeVar("T")


@dataclass(frozen=True)
class ContextOnlyFollowupAnswerRepairSpec:
    project_id: str
    task_id: str
    utterance_id: str
    chapter_id: str
    source_link_id: str
    source_card_id: str
    source_assignment_event_id: str
    expected_source_text: str
    answer_kind: MemoirFollowupAnswerKind
    expected_interpretation_status: str
    expected_task_status: str
    expected_gap_status: str
    derived_story_ids: tuple[str, ...]
    derived_claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class ContextOnlyFollowupAnswerRepairPlan:
    project_id: str
    task_id: str
    utterance_id: str
    chapter_id: str
    answer_kind: str
    source_text: str
    source_link_ids_to_remove: tuple[str, ...]
    source_card_ids_to_delete: tuple[str, ...]
    story_ids_to_delete: tuple[str, ...]
    claim_ids_to_delete: tuple[str, ...]
    source_assignment_event_ids_to_close: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FollowupAnswerRepairMismatch(message)


def _required(value: T | None, message: str) -> T:
    _require(value is not None, message)
    return value


async def _count(db: AsyncSession, model, *conditions) -> int:
    return int(
        await db.scalar(select(func.count()).select_from(model).where(*conditions))
        or 0
    )


async def _validate_story_graph(
    db: AsyncSession,
    *,
    spec: ContextOnlyFollowupAnswerRepairSpec,
) -> tuple[list[MemoirStory], list[MemoirClaim]]:
    story_ids = set(spec.derived_story_ids)
    claim_ids = set(spec.derived_claim_ids)
    _require(bool(story_ids), "必须显式提供要删除的派生 Story")
    _require(bool(claim_ids), "必须显式提供要删除的派生 Claim")

    stories = list(
        (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == spec.project_id,
                    MemoirStory.id.in_(story_ids),
                )
            )
        ).all()
    )
    _require({story.id for story in stories} == story_ids, "派生 Story 与预期不一致")
    _require(
        all(story.created_by == MemoirNarrativeActorType.AI.value for story in stories),
        "拒绝删除非 AI 创建的 Story",
    )
    _require(
        all(
            story.updated_by == MemoirNarrativeActorType.AI.value
            and story.verification_decided_by_user_id is None
            and story.publication_decided_by_user_id is None
            for story in stories
        ),
        "拒绝删除已经过用户处理的 Story",
    )

    story_claims = list(
        (
            await db.scalars(
                select(MemoirStoryClaim).where(
                    MemoirStoryClaim.project_id == spec.project_id,
                    MemoirStoryClaim.story_id.in_(story_ids),
                )
            )
        ).all()
    )
    _require(
        {relation.claim_id for relation in story_claims} == claim_ids,
        "派生 Story 的 Claim 集合与预期不一致",
    )
    _require(
        {relation.story_id for relation in story_claims} == story_ids,
        "派生 Story 缺少 Claim 关系",
    )

    external_story_references = sum(
        [
            await _count(
                db,
                MemoirFollowupTask,
                MemoirFollowupTask.project_id == spec.project_id,
                MemoirFollowupTask.story_id.in_(story_ids),
            ),
            await _count(
                db,
                MemoirNarrativeGap,
                MemoirNarrativeGap.project_id == spec.project_id,
                MemoirNarrativeGap.primary_story_id.in_(story_ids),
            ),
            await _count(
                db,
                MemoirNarrativeGapStory,
                MemoirNarrativeGapStory.project_id == spec.project_id,
                MemoirNarrativeGapStory.story_id.in_(story_ids),
            ),
            await _count(
                db,
                MemoirChapterNodeStory,
                MemoirChapterNodeStory.project_id == spec.project_id,
                MemoirChapterNodeStory.story_id.in_(story_ids),
            ),
            await _count(
                db,
                MemoirDraftBlockStory,
                MemoirDraftBlockStory.project_id == spec.project_id,
                MemoirDraftBlockStory.story_id.in_(story_ids),
            ),
            await _count(
                db,
                MemoirStory,
                MemoirStory.project_id == spec.project_id,
                MemoirStory.merged_into_story_id.in_(story_ids),
                MemoirStory.id.not_in(story_ids),
            ),
        ]
    )
    _require(external_story_references == 0, "派生 Story 已被其他业务数据引用")

    claims = list(
        (
            await db.scalars(
                select(MemoirClaim).where(
                    MemoirClaim.project_id == spec.project_id,
                    MemoirClaim.id.in_(claim_ids),
                )
            )
        ).all()
    )
    _require({claim.id for claim in claims} == claim_ids, "派生 Claim 与预期不一致")
    _require(
        all(claim.created_by == MemoirNarrativeActorType.AI.value for claim in claims),
        "拒绝删除非 AI 创建的 Claim",
    )
    _require(
        all(claim.updated_by == MemoirNarrativeActorType.AI.value for claim in claims),
        "拒绝删除已经过用户处理的 Claim",
    )

    evidence = list(
        (
            await db.scalars(
                select(MemoirClaimEvidence).where(
                    MemoirClaimEvidence.project_id == spec.project_id,
                    MemoirClaimEvidence.claim_id.in_(claim_ids),
                )
            )
        ).all()
    )
    _require({item.claim_id for item in evidence} == claim_ids, "派生 Claim 缺少证据")
    _require(
        all(item.utterance_id == spec.utterance_id for item in evidence),
        "派生 Claim 还包含其他讲述证据",
    )

    story_slot_ids = set(
        (
            await db.scalars(
                select(MemoirStorySlot.id).where(
                    MemoirStorySlot.project_id == spec.project_id,
                    MemoirStorySlot.story_id.in_(story_ids),
                )
            )
        ).all()
    )
    external_claim_references = sum(
        [
            await _count(
                db,
                MemoirStoryClaim,
                MemoirStoryClaim.project_id == spec.project_id,
                MemoirStoryClaim.claim_id.in_(claim_ids),
                MemoirStoryClaim.story_id.not_in(story_ids),
            ),
            await _count(
                db,
                MemoirStorySlotClaim,
                MemoirStorySlotClaim.project_id == spec.project_id,
                MemoirStorySlotClaim.claim_id.in_(claim_ids),
                MemoirStorySlotClaim.story_slot_id.not_in(story_slot_ids),
            ),
            await _count(
                db,
                MemoirFollowupTaskClaim,
                MemoirFollowupTaskClaim.project_id == spec.project_id,
                MemoirFollowupTaskClaim.claim_id.in_(claim_ids),
            ),
            await _count(
                db,
                MemoirNarrativeGapClaim,
                MemoirNarrativeGapClaim.project_id == spec.project_id,
                MemoirNarrativeGapClaim.claim_id.in_(claim_ids),
            ),
            await _count(
                db,
                MemoirDraftBlockClaim,
                MemoirDraftBlockClaim.project_id == spec.project_id,
                MemoirDraftBlockClaim.claim_id.in_(claim_ids),
            ),
            await _count(
                db,
                MemoirClaim,
                MemoirClaim.project_id == spec.project_id,
                MemoirClaim.time_anchor_claim_id.in_(claim_ids),
            ),
            await _count(
                db,
                MemoirClaim,
                MemoirClaim.project_id == spec.project_id,
                MemoirClaim.superseded_by_claim_id.in_(claim_ids),
            ),
        ]
    )
    _require(external_claim_references == 0, "派生 Claim 已被其他业务数据引用")
    return stories, claims


async def inspect_context_only_followup_answer_repair(
    db: AsyncSession,
    *,
    spec: ContextOnlyFollowupAnswerRepairSpec,
) -> ContextOnlyFollowupAnswerRepairPlan:
    _require(
        spec.answer_kind
        in {MemoirFollowupAnswerKind.CLARIFICATION, MemoirFollowupAnswerKind.FACT_RESOLUTION},
        "历史上下文修复只接受解释原文或事实核对",
    )
    _require(bool(spec.expected_source_text.strip()), "必须提供完整的预期原文")

    relation = await db.scalar(
        select(MemoirFollowupTaskAnswer).where(
            MemoirFollowupTaskAnswer.project_id == spec.project_id,
            MemoirFollowupTaskAnswer.task_id == spec.task_id,
            MemoirFollowupTaskAnswer.utterance_id == spec.utterance_id,
        )
    )
    _require(relation is not None, "补全回答关系不存在")
    _require(
        await interview_policy.answer_belongs_to_creation_assignment(
            db,
            utterance_id=spec.utterance_id,
            followup_task_id=spec.task_id,
        ),
        "补全回答缺少采集任务归属",
    )
    _require(
        relation.interpretation_status == spec.expected_interpretation_status,
        "补全回答当前解释状态与预期不一致",
    )

    task = await db.scalar(
        select(MemoirFollowupTask).where(
            MemoirFollowupTask.project_id == spec.project_id,
            MemoirFollowupTask.id == spec.task_id,
        )
    )
    _require(task is not None, "补全任务不存在")
    _require(task.chapter_id == spec.chapter_id, "补全任务所属章节与预期不一致")
    _require(task.status == spec.expected_task_status, "补全任务状态与预期不一致")
    expected_answer_use = {
        MemoirFollowupAnswerKind.CLARIFICATION: MemoirFollowupAnswerUse.CLARIFICATION,
        MemoirFollowupAnswerKind.FACT_RESOLUTION: MemoirFollowupAnswerUse.FACT_RESOLUTION,
    }[spec.answer_kind]
    _require(
        task.answer_use == expected_answer_use.value,
        "补全任务的预期用途与确认结果不一致",
    )

    gap = await db.scalar(
        select(MemoirNarrativeGap).where(
            MemoirNarrativeGap.project_id == spec.project_id,
            MemoirNarrativeGap.id == task.gap_id,
        )
    )
    _require(gap is not None, "补全任务对应的缺口不存在")
    _require(gap.status == spec.expected_gap_status, "补全缺口状态与预期不一致")

    utterance = await db.scalar(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceSession.id == MemoirVoiceUtterance.voice_session_id)
        .where(
            MemoirVoiceSession.project_id == spec.project_id,
            MemoirVoiceUtterance.id == spec.utterance_id,
        )
    )
    _require(utterance is not None, "讲述记录不属于指定项目")
    source_text = (utterance.transcript_edited or "").strip() or (
        utterance.transcript_raw or ""
    ).strip()
    _require(source_text == spec.expected_source_text.strip(), "讲述原文与预期不一致")

    chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == spec.project_id,
            MemoirBookPlanChapter.id == spec.chapter_id,
        )
    )
    _require(chapter is not None, "目标章节不存在")

    active_links = list(
        (
            await db.scalars(
                select(MemoirChapterSourceLink).where(
                    MemoirChapterSourceLink.project_id == spec.project_id,
                    MemoirChapterSourceLink.utterance_id == spec.utterance_id,
                    MemoirChapterSourceLink.status == MemoirChapterSourceLinkStatus.ACTIVE.value,
                )
            )
        ).all()
    )
    _require(
        {link.id for link in active_links} == {spec.source_link_id},
        "讲述的有效章节素材链接与预期不一致",
    )
    source_link = active_links[0]
    _require(source_link.chapter_id == spec.chapter_id, "素材链接所属章节与预期不一致")
    _require(source_link.source_card_id == spec.source_card_id, "素材链接来源卡片与预期不一致")
    other_active_source_count = await _count(
        db,
        MemoirChapterSourceLink,
        MemoirChapterSourceLink.project_id == spec.project_id,
        MemoirChapterSourceLink.chapter_id == spec.chapter_id,
        MemoirChapterSourceLink.status == MemoirChapterSourceLinkStatus.ACTIVE.value,
        MemoirChapterSourceLink.id != spec.source_link_id,
    )
    _require(other_active_source_count > 0, "修复后章节不能没有正文素材")

    source_card = await db.scalar(
        select(MemoirSourceCard).where(
            MemoirSourceCard.project_id == spec.project_id,
            MemoirSourceCard.id == spec.source_card_id,
            MemoirSourceCard.utterance_id == spec.utterance_id,
        )
    )
    _require(source_card is not None, "派生来源卡片与预期不一致")
    source_card_link_count = await _count(
        db,
        MemoirChapterSourceLink,
        MemoirChapterSourceLink.project_id == spec.project_id,
        MemoirChapterSourceLink.source_card_id == spec.source_card_id,
        MemoirChapterSourceLink.id != spec.source_link_id,
    )
    _require(source_card_link_count == 0, "来源卡片仍被其他章节素材引用")

    event = await db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == spec.project_id,
            MemoirAutomationEvent.id == spec.source_assignment_event_id,
        )
    )
    _require(event is not None, "自动归章记录不存在")
    _require(
        event.event_type == MemoirAutomationEventType.SOURCE_ASSIGNED.value
        and event.status == MemoirAutomationEventStatus.COMPLETED.value,
        "自动归章记录状态与预期不一致",
    )
    _require(event.chapter_id == spec.chapter_id, "自动归章记录所属章节与预期不一致")
    _require(set(event.utterance_ids_json or []) == {spec.utterance_id}, "自动归章记录内容与预期不一致")

    stories, claims = await _validate_story_graph(db, spec=spec)
    return ContextOnlyFollowupAnswerRepairPlan(
        project_id=spec.project_id,
        task_id=spec.task_id,
        utterance_id=spec.utterance_id,
        chapter_id=spec.chapter_id,
        answer_kind=spec.answer_kind.value,
        source_text=source_text,
        source_link_ids_to_remove=(source_link.id,),
        source_card_ids_to_delete=(source_card.id,),
        story_ids_to_delete=tuple(sorted(story.id for story in stories)),
        claim_ids_to_delete=tuple(sorted(claim.id for claim in claims)),
        source_assignment_event_ids_to_close=(event.id,),
    )


async def apply_context_only_followup_answer_repair(
    db: AsyncSession,
    *,
    spec: ContextOnlyFollowupAnswerRepairSpec,
) -> ContextOnlyFollowupAnswerRepairPlan:
    plan = await inspect_context_only_followup_answer_repair(db, spec=spec)
    now = datetime.now(UTC).replace(tzinfo=None)

    relation = _required(
        await db.scalar(
            select(MemoirFollowupTaskAnswer).where(
                MemoirFollowupTaskAnswer.project_id == spec.project_id,
                MemoirFollowupTaskAnswer.task_id == spec.task_id,
                MemoirFollowupTaskAnswer.utterance_id == spec.utterance_id,
            )
        ),
        "补全回答关系不存在",
    )
    relation.interpretation_status = MemoirFollowupAnswerInterpretationStatus.RESOLVED.value
    relation.answer_kind = spec.answer_kind.value
    relation.result_role = MemoirFollowupTaskAnswerRole.TARGET_ANSWER.value
    relation.direct_narrative_text = ""
    relation.clarification_text = (
        plan.source_text if spec.answer_kind == MemoirFollowupAnswerKind.CLARIFICATION else ""
    )
    relation.fact_resolution_text = (
        plan.source_text if spec.answer_kind == MemoirFollowupAnswerKind.FACT_RESOLUTION else ""
    )
    relation.confirmation_question = ""
    relation.interpretation_reason = "用户确认该历史补全回答只用于理解章节，不作为正文素材。"
    db.add(relation)
    await db.flush()
    answered = await complete_task_from_semantic_interpretation(
        db,
        project_id=spec.project_id,
        relation=relation,
    )
    _require(answered, "补全任务的预期用途与确认结果不一致")

    utterance = _required(
        await db.get(MemoirVoiceUtterance, spec.utterance_id),
        "讲述记录不存在",
    )
    apply_source_route_disposition(
        utterance,
        MemoirBookSourceRouteResponse(
            material_type="memoir_content",
            action="record_as_context",
            utterance_id=spec.utterance_id,
            source_index=0,
            confidence=1,
            reason="用户确认该回答用于解释章节原意，不作为可发表叙事。",
            assignment_basis="completion_task",
        ),
    )
    db.add(utterance)

    source_link = _required(
        await db.get(MemoirChapterSourceLink, spec.source_link_id),
        "章节素材链接不存在",
    )
    source_link.status = MemoirChapterSourceLinkStatus.REMOVED.value
    source_link.removed_at = now
    source_link.updated_at = now
    source_link.source_card_id = None
    db.add(source_link)

    chapter = _required(
        await db.get(MemoirBookPlanChapter, spec.chapter_id),
        "目标章节不存在",
    )
    pending_ids = pending_source_ids(chapter.metadata_json)
    pending_ids.discard(spec.utterance_id)
    removed_ids = removed_source_ids(chapter.metadata_json)
    removed_ids.add(spec.utterance_id)
    chapter.metadata_json = set_source_change_metadata(
        chapter.metadata_json,
        added_ids=pending_ids,
        removed_ids=removed_ids,
    )
    db.add(chapter)

    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == spec.project_id,
            MemoirBookChapterDraft.chapter_id == spec.chapter_id,
        )
    )
    if draft is not None:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = draft_status_metadata(
            draft.metadata_json,
            SOURCES_CHANGED_STATUS_REASON,
        )
        db.add(draft)
    await draft_blocks.mark_chapter_blocks_stale(
        db,
        project_id=spec.project_id,
        chapter_ids={spec.chapter_id},
    )

    source_card = _required(
        await db.get(MemoirSourceCard, spec.source_card_id),
        "派生来源卡片不存在",
    )
    await db.delete(source_card)
    for story_id in spec.derived_story_ids:
        story = _required(await db.get(MemoirStory, story_id), "派生 Story 不存在")
        await db.delete(story)
    await db.flush()
    for claim_id in spec.derived_claim_ids:
        claim = _required(await db.get(MemoirClaim, claim_id), "派生 Claim 不存在")
        await db.delete(claim)

    event = _required(
        await db.get(MemoirAutomationEvent, spec.source_assignment_event_id),
        "自动归章记录不存在",
    )
    event.summary_text = "一条解释性回答已从章节正文素材中移除。"
    event.metadata_json = {
        **(event.metadata_json or {}),
        "acknowledged_at": now.replace(tzinfo=UTC).isoformat(),
        "historical_repair": {
            "answer_kind": spec.answer_kind.value,
            "repaired_at": now.replace(tzinfo=UTC).isoformat(),
            "reason": "user_confirmed_context_only_followup_answer",
        },
    }
    db.add(event)
    await db.flush()

    return plan
