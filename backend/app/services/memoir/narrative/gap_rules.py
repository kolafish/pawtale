from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirClaimEvidenceRole,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirDraftBlock,
    MemoirDraftBlockVersion,
    MemoirStory,
    MemoirStoryClaim,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.narrative.claim_usage_policy import (
    claim_usable_for_publication,
)


@dataclass(frozen=True)
class ChapterAuditDraftBlock:
    id: str
    node_id: str
    block_role: str
    sort_order: int
    editorial_status: str
    freshness_status: str
    current_version_id: str
    content: str
    claim_ids: tuple[str, ...] = ()
    story_ids: tuple[str, ...] = ()


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
    draft_blocks: tuple[ChapterAuditDraftBlock, ...] = ()


async def load_chapter_audit_contexts(
    db: AsyncSession,
    *,
    project_id: str,
    chapters: list[MemoirBookPlanChapter],
) -> dict[str, ChapterAuditContext]:
    if any(chapter.project_id != project_id for chapter in chapters):
        raise ValueError("chapter does not belong to the project")
    chapter_ids = [chapter.id for chapter in chapters]
    if not chapter_ids:
        return {}

    contract_rows = list(
        (
            await db.scalars(
                select(MemoirChapterContract)
                .where(
                    MemoirChapterContract.project_id == project_id,
                    MemoirChapterContract.chapter_id.in_(chapter_ids),
                    MemoirChapterContract.status.in_(
                        [
                            MemoirChapterContractStatus.CONFIRMED.value,
                            MemoirChapterContractStatus.DRAFT.value,
                        ]
                    ),
                )
                .execution_options(populate_existing=True)
                .order_by(
                    MemoirChapterContract.chapter_id,
                    MemoirChapterContract.version.desc(),
                    (
                        MemoirChapterContract.status
                        == MemoirChapterContractStatus.CONFIRMED.value
                    ).desc(),
                )
            )
        ).all()
    )
    contracts_by_chapter: dict[str, MemoirChapterContract] = {}
    for contract in contract_rows:
        contracts_by_chapter.setdefault(contract.chapter_id, contract)

    contract_ids = [contract.id for contract in contracts_by_chapter.values()]
    node_rows = (
        list(
            (
                await db.scalars(
                    select(MemoirChapterNode)
                    .where(
                        MemoirChapterNode.project_id == project_id,
                        MemoirChapterNode.contract_id.in_(contract_ids),
                    )
                    .execution_options(populate_existing=True)
                    .order_by(
                        MemoirChapterNode.contract_id,
                        MemoirChapterNode.sort_order,
                        MemoirChapterNode.id,
                    )
                )
            ).all()
        )
        if contract_ids
        else []
    )
    nodes_by_contract: dict[str, list[MemoirChapterNode]] = defaultdict(list)
    for node in node_rows:
        nodes_by_contract[node.contract_id].append(node)

    node_ids = [node.id for node in node_rows]
    link_rows = (
        list(
            (
                await db.scalars(
                    select(MemoirChapterNodeStory)
                    .where(
                        MemoirChapterNodeStory.project_id == project_id,
                        MemoirChapterNodeStory.node_id.in_(node_ids),
                    )
                    .execution_options(populate_existing=True)
                    .order_by(
                        MemoirChapterNodeStory.node_id,
                        MemoirChapterNodeStory.sort_order,
                        MemoirChapterNodeStory.id,
                    )
                )
            ).all()
        )
        if node_ids
        else []
    )
    links_by_node: dict[str, list[MemoirChapterNodeStory]] = defaultdict(list)
    for link in link_rows:
        links_by_node[link.node_id].append(link)

    story_ids = {link.story_id for link in link_rows}
    stories = (
        {
            story.id: story
            for story in (
                await db.scalars(
                    select(MemoirStory).where(
                        MemoirStory.project_id == project_id,
                        MemoirStory.id.in_(story_ids),
                        MemoirStory.lifecycle_status
                        == MemoirStoryLifecycleStatus.ACTIVE.value,
                    ).execution_options(populate_existing=True)
                )
            ).all()
        }
        if story_ids
        else {}
    )
    story_claim_links = (
        list(
            (
                await db.scalars(
                    select(MemoirStoryClaim)
                    .where(
                        MemoirStoryClaim.project_id == project_id,
                        MemoirStoryClaim.story_id.in_(story_ids),
                    )
                    .execution_options(populate_existing=True)
                    .order_by(
                        MemoirStoryClaim.story_id,
                        MemoirStoryClaim.sort_order,
                    )
                )
            ).all()
        )
        if story_ids
        else []
    )
    story_claims: dict[str, list[MemoirStoryClaim]] = defaultdict(list)
    for link in story_claim_links:
        story_claims[link.story_id].append(link)

    claim_ids = {link.claim_id for link in story_claim_links}
    claims = (
        {
            claim.id: claim
            for claim in (
                await db.scalars(
                    select(MemoirClaim).where(
                        MemoirClaim.project_id == project_id,
                        MemoirClaim.id.in_(claim_ids),
                    ).execution_options(populate_existing=True)
                )
            ).all()
        }
        if claim_ids
        else {}
    )
    evidence_counts = (
        {
            claim_id: int(count)
            for claim_id, count in (
                await db.execute(
                    select(
                        MemoirClaimEvidence.claim_id,
                        func.count(MemoirClaimEvidence.id),
                    )
                    .where(
                        MemoirClaimEvidence.project_id == project_id,
                        MemoirClaimEvidence.claim_id.in_(claim_ids),
                        MemoirClaimEvidence.evidence_role
                        == MemoirClaimEvidenceRole.SUPPORTS.value,
                    )
                    .group_by(MemoirClaimEvidence.claim_id)
                )
            ).all()
        }
        if claim_ids
        else {}
    )
    draft_rows = (
        await db.execute(
            select(MemoirDraftBlock, MemoirDraftBlockVersion)
            .options(
                selectinload(MemoirDraftBlockVersion.claims),
                selectinload(MemoirDraftBlockVersion.stories),
            )
            .join(
                MemoirDraftBlockVersion,
                (MemoirDraftBlockVersion.project_id == MemoirDraftBlock.project_id)
                & (MemoirDraftBlockVersion.id == MemoirDraftBlock.current_version_id),
            )
            .where(
                MemoirDraftBlock.project_id == project_id,
                MemoirDraftBlock.chapter_id.in_(chapter_ids),
            )
            .execution_options(populate_existing=True)
            .order_by(
                MemoirDraftBlock.chapter_id,
                MemoirDraftBlock.sort_order,
                MemoirDraftBlock.id,
            )
        )
    ).all()
    draft_blocks_by_chapter: dict[str, list[ChapterAuditDraftBlock]] = defaultdict(list)
    for block, version in draft_rows:
        draft_blocks_by_chapter[block.chapter_id].append(
            ChapterAuditDraftBlock(
                id=block.id,
                node_id=block.node_id,
                block_role=block.block_role,
                sort_order=block.sort_order,
                editorial_status=block.editorial_status,
                freshness_status=block.freshness_status,
                current_version_id=version.id,
                content=version.content,
                claim_ids=tuple(relation.claim_id for relation in version.claims),
                story_ids=tuple(relation.story_id for relation in version.stories),
            )
        )

    contexts: dict[str, ChapterAuditContext] = {}
    for chapter in chapters:
        contract = contracts_by_chapter.get(chapter.id)
        nodes = nodes_by_contract.get(contract.id, []) if contract else []
        links = [
            link
            for node in nodes
            for link in links_by_node.get(node.id, [])
        ]
        chapter_story_ids = {link.story_id for link in links}
        contexts[chapter.id] = ChapterAuditContext(
            chapter=chapter,
            contract=contract,
            nodes=list(nodes),
            links=links,
            stories={
                story_id: stories[story_id]
                for story_id in chapter_story_ids
                if story_id in stories
            },
            story_claims={
                story_id: story_claims.get(story_id, [])
                for story_id in chapter_story_ids
            },
            claims={
                link.claim_id: claims[link.claim_id]
                for story_id in chapter_story_ids
                for link in story_claims.get(story_id, [])
                if link.claim_id in claims
            },
            evidence_counts={
                link.claim_id: evidence_counts.get(link.claim_id, 0)
                for story_id in chapter_story_ids
                for link in story_claims.get(story_id, [])
                if link.claim_id in claims
            },
            draft_blocks=tuple(draft_blocks_by_chapter.get(chapter.id, [])),
        )
    return contexts


async def load_chapter_audit_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> ChapterAuditContext:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
        .execution_options(populate_existing=True)
    )
    if chapter is None:
        raise ValueError("chapter does not belong to the project")
    return (
        await load_chapter_audit_contexts(
            db,
            project_id=project_id,
            chapters=[chapter],
        )
    )[chapter.id]


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
        involved_story_ids=set(story_ids or []),
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
                    and story.verification_status
                    not in {
                        MemoirStoryVerificationStatus.DISPUTED.value,
                        MemoirStoryVerificationStatus.REJECTED.value,
                    }
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

    for story_id, story in context.stories.items():
        if (
            story.publication_status != MemoirStoryPublicationStatus.ELIGIBLE.value
            or story.verification_status == MemoirStoryVerificationStatus.REJECTED.value
        ):
            continue
        for story_claim in context.story_claims.get(story_id, []):
            claim = context.claims.get(story_claim.claim_id)
            if (
                claim is None
                or not claim_usable_for_publication(claim)
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
                    involved_chapter_ids={context.chapter.id},
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
