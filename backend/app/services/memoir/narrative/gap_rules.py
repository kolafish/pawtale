from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirClaimEvidenceRole,
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRelationRole,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStorySlotStatus,
    MemoirStoryVerificationStatus,
)
from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStorySlot,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.schemas.memoir_narrative.story_slots import story_slot_schema


@dataclass(frozen=True)
class ChapterAuditContext:
    chapter: MemoirBookPlanChapter
    contract: MemoirChapterContract | None
    nodes: list[MemoirChapterNode]
    links: list[MemoirChapterNodeStory]
    stories: dict[str, MemoirStory]
    story_claims: dict[str, list[MemoirStoryClaim]]
    claims: dict[str, MemoirClaim]
    evidence_counts: dict[str, int]
    slots: dict[str, dict[str, MemoirStorySlot]]


SLOT_LABELS = {
    "background": "背景",
    "trigger": "起因",
    "people": "人物",
    "difficulty": "困难",
    "actions": "行动",
    "turning_point": "转折",
    "outcome": "结果",
    "reflection": "感受",
    "meeting": "相识",
    "person_portrait": "人物印象",
    "shared_experience": "共同经历",
    "relationship_change": "关系变化",
    "representative_scene": "代表场景",
    "entry_reason": "进入原因",
    "responsibilities": "承担的责任",
    "representative_events": "代表事件",
    "ability_and_character": "能力与性格",
    "transition": "阶段变化",
    "theme_definition": "主题含义",
    "supporting_cases": "支撑故事",
    "contrast_or_failure": "对照或挫折",
    "continuity": "延续与影响",
}


async def load_chapter_audit_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> ChapterAuditContext:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter is None:
        raise ValueError("chapter does not belong to the project")
    contract = await db.scalar(
        select(MemoirChapterContract)
        .where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == chapter_id,
            MemoirChapterContract.status.in_(
                [
                    MemoirChapterContractStatus.CONFIRMED.value,
                    MemoirChapterContractStatus.DRAFT.value,
                ]
            ),
        )
        .order_by(
            MemoirChapterContract.version.desc(),
            (MemoirChapterContract.status == MemoirChapterContractStatus.CONFIRMED.value).desc(),
        )
    )
    nodes: list[MemoirChapterNode] = []
    links: list[MemoirChapterNodeStory] = []
    if contract is not None:
        nodes = list(
            (
                await db.scalars(
                    select(MemoirChapterNode)
                    .where(
                        MemoirChapterNode.project_id == project_id,
                        MemoirChapterNode.contract_id == contract.id,
                    )
                    .order_by(MemoirChapterNode.sort_order, MemoirChapterNode.id)
                )
            ).all()
        )
        node_ids = [node.id for node in nodes]
        if node_ids:
            links = list(
                (
                    await db.scalars(
                        select(MemoirChapterNodeStory)
                        .where(
                            MemoirChapterNodeStory.project_id == project_id,
                            MemoirChapterNodeStory.node_id.in_(node_ids),
                        )
                        .order_by(
                            MemoirChapterNodeStory.node_id,
                            MemoirChapterNodeStory.sort_order,
                            MemoirChapterNodeStory.id,
                        )
                    )
                ).all()
            )
    story_ids = {link.story_id for link in links}
    stories = {
        story.id: story
        for story in (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.id.in_(story_ids),
                    MemoirStory.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value,
                )
            )
        ).all()
    } if story_ids else {}
    story_claim_links = list(
        (
            await db.scalars(
                select(MemoirStoryClaim)
                .where(
                    MemoirStoryClaim.project_id == project_id,
                    MemoirStoryClaim.story_id.in_(story_ids),
                )
                .order_by(MemoirStoryClaim.story_id, MemoirStoryClaim.sort_order)
            )
        ).all()
    ) if story_ids else []
    story_claims: dict[str, list[MemoirStoryClaim]] = defaultdict(list)
    for link in story_claim_links:
        story_claims[link.story_id].append(link)
    claim_ids = {link.claim_id for link in story_claim_links}
    claims = {
        claim.id: claim
        for claim in (
            await db.scalars(
                select(MemoirClaim).where(
                    MemoirClaim.project_id == project_id,
                    MemoirClaim.id.in_(claim_ids),
                )
            )
        ).all()
    } if claim_ids else {}
    evidence_counts = {
        claim_id: int(count)
        for claim_id, count in (
            await db.execute(
                select(MemoirClaimEvidence.claim_id, func.count(MemoirClaimEvidence.id))
                .where(
                    MemoirClaimEvidence.project_id == project_id,
                    MemoirClaimEvidence.claim_id.in_(claim_ids),
                    MemoirClaimEvidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value,
                )
                .group_by(MemoirClaimEvidence.claim_id)
            )
        ).all()
    } if claim_ids else {}
    story_slots: dict[str, dict[str, MemoirStorySlot]] = defaultdict(dict)
    if story_ids:
        for slot in (
            await db.scalars(
                select(MemoirStorySlot).where(
                    MemoirStorySlot.project_id == project_id,
                    MemoirStorySlot.story_id.in_(story_ids),
                )
            )
        ).all():
            story_slots[slot.story_id][slot.slot_key] = slot
    return ChapterAuditContext(
        chapter=chapter,
        contract=contract,
        nodes=nodes,
        links=links,
        stories=stories,
        story_claims=dict(story_claims),
        claims=claims,
        evidence_counts=evidence_counts,
        slots=dict(story_slots),
    )


def _chapter_candidate(
    context: ChapterAuditContext,
    *,
    gap_type: MemoirNarrativeGapType,
    severity: MemoirNarrativeGapSeverity,
    target_slot: str,
    title: str,
    description: str,
    story_ids: list[str] | None = None,
    claim_roles: dict[str, MemoirNarrativeGapClaimRole] | None = None,
) -> MemoirNarrativeGapCandidate:
    return MemoirNarrativeGapCandidate(
        scope_type=MemoirNarrativeGapScopeType.CHAPTER,
        primary_chapter_id=context.chapter.id,
        gap_type=gap_type,
        severity=severity,
        target_slot=target_slot,
        title=title,
        description=description,
        detected_by=MemoirNarrativeGapDetectedBy.RULES,
        claim_roles=claim_roles or {},
        story_roles={
            story_id: MemoirNarrativeGapRelationRole.INVOLVED
            for story_id in story_ids or []
        },
    )


def evaluate_deterministic_gaps(
    context: ChapterAuditContext,
) -> list[MemoirNarrativeGapCandidate]:
    candidates: list[MemoirNarrativeGapCandidate] = []
    if context.contract is None:
        return [
            _chapter_candidate(
                context,
                gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot="chapter_contract",
                title="章节还没有结构契约",
                description=(
                    "先明确本章目标、读者收获和必要结构，再判断内容是否完整。"
                ),
            )
        ]

    links_by_node: dict[str, list[MemoirChapterNodeStory]] = defaultdict(list)
    for link in context.links:
        links_by_node[link.node_id].append(link)
    nodes_by_id = {node.id: node for node in context.nodes}

    for node in context.nodes:
        if (
            node.required
            and node.node_type != MemoirChapterNodeType.STORY.value
            and node.status not in {
                MemoirChapterNodeStatus.READY.value,
                MemoirChapterNodeStatus.EXCLUDED.value,
            }
        ):
            candidates.append(
                _chapter_candidate(
                    context,
                    gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                    severity=MemoirNarrativeGapSeverity.HIGH,
                    target_slot=f"node:{node.id}:readiness",
                    title=f"必要结构“{node.title}”尚未完成",
                    description=node.purpose,
                )
            )
        if (
            node.required
            and node.node_type == MemoirChapterNodeType.STORY.value
            and not links_by_node.get(node.id)
        ):
            candidates.append(
                _chapter_candidate(
                    context,
                    gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                    severity=MemoirNarrativeGapSeverity.HIGH,
                    target_slot=f"node:{node.id}:story",
                    title=f"故事节点“{node.title}”还没有故事",
                    description="该必要节点需要关联一个有事实证据的 Story。",
                )
            )

        if node.required and node.node_type == MemoirChapterNodeType.STORY.value:
            required_links = [link for link in links_by_node.get(node.id, []) if link.required]
            if not required_links:
                required_links = links_by_node.get(node.id, [])
            for link in required_links:
                story = context.stories.get(link.story_id)
                if story is not None and (
                    story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
                    and story.verification_status != MemoirStoryVerificationStatus.REJECTED.value
                ):
                    continue
                candidates.append(
                    _chapter_candidate(
                        context,
                        gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                        severity=MemoirNarrativeGapSeverity.HIGH,
                        target_slot=f"node:{node.id}:eligible-story:{link.story_id}",
                        title=f"故事节点“{node.title}”缺少可用于成书的故事",
                        description=(
                            "当前关联 Story 已合并、归档、排除、设为私密或被人工否定。"
                        ),
                        story_ids=[link.story_id] if story is not None else [],
                    )
                )

    required_story_ids = {
        link.story_id
        for link in context.links
        if link.required
        or (
            nodes_by_id.get(link.node_id) is not None
            and nodes_by_id[link.node_id].required
        )
    }
    for story_id, story in context.stories.items():
        if (
            story.publication_status != MemoirStoryPublicationStatus.ELIGIBLE.value
            or story.verification_status == MemoirStoryVerificationStatus.REJECTED.value
        ):
            continue
        required_slots = story_slot_schema(story.story_type, story.slot_schema_version).required_slot_keys
        by_key = context.slots.get(story_id, {})
        for slot_key in required_slots:
            slot = by_key.get(slot_key)
            status = slot.status if slot else MemoirStorySlotStatus.MISSING.value
            if status not in {
                MemoirStorySlotStatus.MISSING.value,
                MemoirStorySlotStatus.PARTIAL.value,
            }:
                continue
            severity = (
                MemoirNarrativeGapSeverity.HIGH
                if story_id in required_story_ids and status == MemoirStorySlotStatus.MISSING.value
                else MemoirNarrativeGapSeverity.MEDIUM
            )
            candidates.append(
                MemoirNarrativeGapCandidate(
                    scope_type=MemoirNarrativeGapScopeType.STORY,
                    primary_story_id=story_id,
                    gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                    severity=severity,
                    target_slot=slot_key,
                    title=f"“{story.title}”缺少{SLOT_LABELS.get(slot_key, slot_key)}信息",
                    description=(
                        slot.assessment_summary
                        if slot
                        else "该必要故事槽位尚无可引用事实。"
                    ),
                    detected_by=MemoirNarrativeGapDetectedBy.RULES,
                    story_roles={story_id: MemoirNarrativeGapRelationRole.PRIMARY},
                    chapter_roles={context.chapter.id: MemoirNarrativeGapRelationRole.INVOLVED},
                )
            )
        for story_claim in context.story_claims.get(story_id, []):
            claim = context.claims.get(story_claim.claim_id)
            if (
                claim is None
                or claim.publication_status != MemoirClaimPublicationStatus.ELIGIBLE.value
                or claim.verification_status
                in {
                    MemoirClaimVerificationStatus.DISPUTED.value,
                    MemoirClaimVerificationStatus.REJECTED.value,
                }
                or claim.superseded_by_claim_id
                or context.evidence_counts.get(claim.id, 0) > 0
            ):
                continue
            candidates.append(
                MemoirNarrativeGapCandidate(
                    scope_type=MemoirNarrativeGapScopeType.STORY,
                    primary_story_id=story_id,
                    gap_type=MemoirNarrativeGapType.UNSUPPORTED_FACT,
                    severity=(
                        MemoirNarrativeGapSeverity.HIGH
                        if story_claim.is_core
                        else MemoirNarrativeGapSeverity.MEDIUM
                    ),
                    target_slot=f"claim:{claim.id}:evidence",
                    title=f"“{story.title}”有一条事实缺少证据",
                    description=claim.statement,
                    detected_by=MemoirNarrativeGapDetectedBy.RULES,
                    claim_roles={claim.id: MemoirNarrativeGapClaimRole.UNSUPPORTED_CANDIDATE},
                    story_roles={story_id: MemoirNarrativeGapRelationRole.PRIMARY},
                    chapter_roles={context.chapter.id: MemoirNarrativeGapRelationRole.INVOLVED},
                )
            )

    primary_counts = Counter(
        link.story_id
        for link in context.links
        if link.story_role == MemoirChapterNodeStoryRole.PRIMARY.value
    )
    for story_id, count in primary_counts.items():
        if count <= 1:
            continue
        story = context.stories.get(story_id)
        candidates.append(
            _chapter_candidate(
                context,
                gap_type=MemoirNarrativeGapType.REPETITION,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                target_slot=f"primary-story:{story_id}",
                title=f"“{story.title if story else '同一故事'}”被多个主节点重复承载",
                description="同一章通常只应由一个 primary 节点承载核心 Story。",
                story_ids=[story_id],
            )
        )

    ordered_story_ids = list(
        dict.fromkeys(
            link.story_id
            for node in context.nodes
            for link in links_by_node.get(node.id, [])
            if link.story_id in context.stories
        )
    )
    previous: MemoirStory | None = None
    for story_id in ordered_story_ids:
        current = context.stories[story_id]
        if (
            previous is not None
            and previous.time_start is not None
            and current.time_start is not None
            and current.time_start < previous.time_start
        ):
            candidates.append(
                _chapter_candidate(
                    context,
                    gap_type=MemoirNarrativeGapType.CHRONOLOGY,
                    severity=MemoirNarrativeGapSeverity.MEDIUM,
                    target_slot=f"story-order:{previous.id}:{current.id}",
                    title="章节故事顺序与明确时间相反",
                    description=(
                        f"“{current.title}”的明确时间早于前一个故事"
                        f"“{previous.title}”。"
                    ),
                    story_ids=[previous.id, current.id],
                )
            )
        elif (
            previous is not None
            and previous.time_end is not None
            and current.time_start is not None
            and current.time_start <= previous.time_end
        ):
            candidates.append(
                _chapter_candidate(
                    context,
                    gap_type=MemoirNarrativeGapType.CHRONOLOGY,
                    severity=MemoirNarrativeGapSeverity.MEDIUM,
                    target_slot=f"story-overlap:{previous.id}:{current.id}",
                    title="相邻故事的明确时间范围重叠",
                    description=(
                        f"“{previous.title}”和“{current.title}”的时间范围重叠，"
                        "需要确认叙述顺序。"
                    ),
                    story_ids=[previous.id, current.id],
                )
            )
        previous = current
    return candidates
