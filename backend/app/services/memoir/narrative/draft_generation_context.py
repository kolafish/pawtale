from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeType,
    MemoirClaimEvidenceRole,
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirDraftBlockClaimUsageRole,
    MemoirDraftBlockRole,
    MemoirDraftBlockStoryUsageRole,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import MemoirBookPlanChapterStatus
from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirClaim,
    MemoirDraftBlock,
    MemoirDraftBlockVersion,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.schemas.memoir_narrative import (
    MemoirDraftBlockClaimSource,
    MemoirDraftBlockStorySource,
)
from app.services.memoir.narrative.draft_block_ai import (
    DraftBlockClaimInput,
    DraftBlockEvidenceInput,
    DraftBlockGenerationInput,
    DraftBlockStoryInput,
)


MAX_ADJACENT_CONTEXT_CHARS = 260


@dataclass(frozen=True)
class DraftNodeGenerationContext:
    node: MemoirChapterNode
    ai_input: DraftBlockGenerationInput
    claim_sources: dict[str, MemoirDraftBlockClaimSource]
    story_sources_by_claim: dict[str, list[MemoirDraftBlockStorySource]]

    def sources_for_claims(
        self,
        claim_ids: list[str],
    ) -> tuple[list[MemoirDraftBlockClaimSource], list[MemoirDraftBlockStorySource]]:
        claims = [self.claim_sources[claim_id] for claim_id in claim_ids]
        stories: list[MemoirDraftBlockStorySource] = []
        seen_story_ids: set[str] = set()
        for claim_id in claim_ids:
            for story in self.story_sources_by_claim.get(claim_id, []):
                if story.story_id in seen_story_ids:
                    continue
                seen_story_ids.add(story.story_id)
                stories.append(story)
        return claims, stories


def block_role_for_node(node_type: str) -> MemoirDraftBlockRole:
    return {
        MemoirChapterNodeType.OPENING.value: MemoirDraftBlockRole.OPENING,
        MemoirChapterNodeType.BACKGROUND.value: MemoirDraftBlockRole.FACTUAL,
        MemoirChapterNodeType.STORY.value: MemoirDraftBlockRole.FACTUAL,
        MemoirChapterNodeType.TRANSITION.value: MemoirDraftBlockRole.TRANSITION,
        MemoirChapterNodeType.REFLECTION.value: MemoirDraftBlockRole.REFLECTION,
        MemoirChapterNodeType.CLOSING.value: MemoirDraftBlockRole.CLOSING,
    }[node_type]


def _claim_usage_role(link: MemoirStoryClaim) -> MemoirDraftBlockClaimUsageRole:
    if link.is_core:
        return MemoirDraftBlockClaimUsageRole.CORE
    if link.narrative_role in {"background", "context"}:
        return MemoirDraftBlockClaimUsageRole.CONTEXT
    return MemoirDraftBlockClaimUsageRole.DETAIL


def _story_usage_role(story_role: str) -> MemoirDraftBlockStoryUsageRole:
    return {
        "primary": MemoirDraftBlockStoryUsageRole.PRIMARY,
        "supporting": MemoirDraftBlockStoryUsageRole.SUPPORTING,
        "contrast": MemoirDraftBlockStoryUsageRole.CONTRAST,
        "transition_context": MemoirDraftBlockStoryUsageRole.SUPPORTING,
    }[story_role]


def _eligible_story(story: MemoirStory) -> bool:
    return (
        story.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value
        and story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
        and story.verification_status
        not in {
            MemoirStoryVerificationStatus.DISPUTED.value,
            MemoirStoryVerificationStatus.REJECTED.value,
        }
    )


def _eligible_claim(claim: MemoirClaim) -> bool:
    return (
        claim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value
        and claim.verification_status
        not in {
            MemoirClaimVerificationStatus.DISPUTED.value,
            MemoirClaimVerificationStatus.REJECTED.value,
        }
        and claim.superseded_by_claim_id is None
        and any(
            evidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value
            and bool((evidence.quote or evidence.evidence_note or "").strip())
            for evidence in claim.evidence
        )
    )


async def load_confirmed_contract_nodes(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> tuple[MemoirChapterContract, list[MemoirChapterNode]]:
    contract = await db.scalar(
        select(MemoirChapterContract)
        .join(
            MemoirBookPlanChapter,
            MemoirBookPlanChapter.id == MemoirChapterContract.chapter_id,
        )
        .where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == chapter_id,
            MemoirChapterContract.status == MemoirChapterContractStatus.CONFIRMED.value,
            MemoirBookPlanChapter.status == MemoirBookPlanChapterStatus.ACTIVE.value,
        )
    )
    if contract is None:
        raise ValueError("confirm the chapter contract before generating draft blocks")
    nodes = list(
        (
            await db.scalars(
                select(MemoirChapterNode)
                .options(selectinload(MemoirChapterNode.stories))
                .where(
                    MemoirChapterNode.project_id == project_id,
                    MemoirChapterNode.chapter_id == chapter_id,
                    MemoirChapterNode.contract_id == contract.id,
                )
                .order_by(MemoirChapterNode.sort_order, MemoirChapterNode.id)
            )
        ).all()
    )
    return contract, nodes


async def _adjacent_contexts(
    db: AsyncSession,
    *,
    project_id: str,
    nodes: list[MemoirChapterNode],
    node: MemoirChapterNode,
) -> tuple[str, str]:
    node_ids = [item.id for item in nodes]
    rows = (
        await db.execute(
            select(MemoirDraftBlock.node_id, MemoirDraftBlockVersion.content)
            .join(
                MemoirDraftBlockVersion,
                MemoirDraftBlockVersion.id == MemoirDraftBlock.current_version_id,
            )
            .where(
                MemoirDraftBlock.project_id == project_id,
                MemoirDraftBlock.node_id.in_(node_ids),
            )
            .order_by(MemoirDraftBlock.sort_order, MemoirDraftBlock.id)
        )
    ).all()
    content_by_node: dict[str, list[str]] = {}
    for node_id, content in rows:
        if node_id:
            content_by_node.setdefault(str(node_id), []).append(str(content).strip())
    position = next(index for index, item in enumerate(nodes) if item.id == node.id)
    previous = ""
    for candidate in reversed(nodes[:position]):
        if content_by_node.get(candidate.id):
            previous = content_by_node[candidate.id][-1]
            break
    following = ""
    for candidate in nodes[position + 1 :]:
        if content_by_node.get(candidate.id):
            following = content_by_node[candidate.id][0]
            break
    return previous[-MAX_ADJACENT_CONTEXT_CHARS:], following[:MAX_ADJACENT_CONTEXT_CHARS]


async def build_node_generation_context(
    db: AsyncSession,
    *,
    project_id: str,
    contract: MemoirChapterContract,
    nodes: list[MemoirChapterNode],
    node: MemoirChapterNode,
    feedback: str = "",
) -> DraftNodeGenerationContext:
    role = block_role_for_node(node.node_type)
    previous_context, next_context = await _adjacent_contexts(
        db,
        project_id=project_id,
        nodes=nodes,
        node=node,
    )
    node_links = sorted(node.stories, key=lambda item: (item.sort_order, item.id))
    story_ids = [link.story_id for link in node_links]
    stories = {
        story.id: story
        for story in (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.id.in_(story_ids),
                )
            )
        ).all()
    } if story_ids and role != MemoirDraftBlockRole.TRANSITION else {}
    eligible_story_ids = {
        story_id for story_id, story in stories.items() if _eligible_story(story)
    }
    revisions: dict[str, MemoirStoryRevision] = {}
    if eligible_story_ids:
        revision_rows = (
            await db.scalars(
                select(MemoirStoryRevision).where(
                    MemoirStoryRevision.project_id == project_id,
                    MemoirStoryRevision.story_id.in_(eligible_story_ids),
                )
                .order_by(
                    MemoirStoryRevision.story_id,
                    MemoirStoryRevision.story_version.desc(),
                )
            )
        ).all()
        for revision in revision_rows:
            revisions.setdefault(revision.story_id, revision)
    story_claim_links = list(
        (
            await db.scalars(
                select(MemoirStoryClaim).where(
                    MemoirStoryClaim.project_id == project_id,
                    MemoirStoryClaim.story_id.in_(eligible_story_ids),
                )
                .order_by(MemoirStoryClaim.story_id, MemoirStoryClaim.sort_order)
            )
        ).all()
    ) if eligible_story_ids else []
    claim_ids = {link.claim_id for link in story_claim_links}
    claims = {
        claim.id: claim
        for claim in (
            await db.scalars(
                select(MemoirClaim)
                .options(selectinload(MemoirClaim.evidence))
                .where(
                    MemoirClaim.project_id == project_id,
                    MemoirClaim.id.in_(claim_ids),
                )
            )
        ).unique().all()
        if _eligible_claim(claim)
    } if claim_ids else {}

    links_by_story: dict[str, list[MemoirStoryClaim]] = {}
    for link in story_claim_links:
        if link.claim_id in claims:
            links_by_story.setdefault(link.story_id, []).append(link)

    prompt_stories: list[DraftBlockStoryInput] = []
    claim_sources: dict[str, MemoirDraftBlockClaimSource] = {}
    story_sources_by_claim: dict[str, list[MemoirDraftBlockStorySource]] = {}
    for node_link in node_links:
        story = stories.get(node_link.story_id)
        revision = revisions.get(node_link.story_id)
        if (
            story is None
            or revision is None
            or revision.story_version != story.version
        ):
            continue
        prompt_claims: list[DraftBlockClaimInput] = []
        for story_claim in links_by_story.get(story.id, []):
            claim = claims[story_claim.claim_id]
            prompt_claims.append(
                DraftBlockClaimInput(
                    claim_id=claim.id,
                    statement=claim.statement,
                    certainty=claim.certainty,
                    verification_status=claim.verification_status,
                    narrative_role=story_claim.narrative_role,
                    is_core=story_claim.is_core,
                    evidence=[
                        DraftBlockEvidenceInput(
                            evidence_id=evidence.id,
                            quote=(evidence.quote or "").strip()[:320],
                            evidence_note=(evidence.evidence_note or "").strip()[:320],
                        )
                        for evidence in sorted(
                            claim.evidence,
                            key=lambda item: (item.created_at, item.id),
                        )
                        if evidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value
                        and (evidence.quote or evidence.evidence_note or "").strip()
                    ][:2],
                )
            )
            claim_sources.setdefault(
                claim.id,
                MemoirDraftBlockClaimSource(
                    claim_id=claim.id,
                    usage_role=_claim_usage_role(story_claim),
                ),
            )
            story_sources_by_claim.setdefault(claim.id, []).append(
                MemoirDraftBlockStorySource(
                    story_id=story.id,
                    story_revision_id=revision.id,
                    usage_role=_story_usage_role(node_link.story_role),
                )
            )
        if prompt_claims:
            prompt_stories.append(
                DraftBlockStoryInput(
                    story_id=story.id,
                    story_revision_id=revision.id,
                    title=story.title,
                    summary=story.summary,
                    story_role=node_link.story_role,
                    claims=prompt_claims,
                )
            )

    if role == MemoirDraftBlockRole.FACTUAL and not claim_sources:
        raise ValueError(f"ready factual node has no eligible claim evidence: {node.title}")
    return DraftNodeGenerationContext(
        node=node,
        ai_input=DraftBlockGenerationInput(
            contract_id=contract.id,
            contract_objective=contract.objective,
            reader_takeaway=contract.reader_takeaway,
            time_scope_text=contract.time_scope_text,
            constraints=(
                contract.constraints_json
                if isinstance(contract.constraints_json, dict)
                else {}
            ),
            node_id=node.id,
            node_type=node.node_type,
            node_title=node.title,
            node_purpose=node.purpose,
            block_role=role,
            stories=prompt_stories,
            previous_block_context=previous_context,
            next_block_context=next_context,
            feedback=feedback.strip(),
        ),
        claim_sources=claim_sources,
        story_sources_by_claim=story_sources_by_claim,
    )


def select_generation_nodes(
    nodes: list[MemoirChapterNode],
    *,
    requested_node_ids: list[str],
) -> list[MemoirChapterNode]:
    by_id = {node.id: node for node in nodes}
    requested = list(dict.fromkeys(requested_node_ids))
    missing = set(requested) - set(by_id)
    if missing:
        raise ValueError("requested draft nodes do not belong to the confirmed contract")
    selected = [by_id[node_id] for node_id in requested] if requested else nodes
    not_ready = [
        node.title
        for node in selected
        if node.status != MemoirChapterNodeStatus.READY.value
    ]
    if requested and not_ready:
        raise ValueError("requested draft nodes are not ready: " + ", ".join(not_ready))
    return [
        node
        for node in selected
        if node.status == MemoirChapterNodeStatus.READY.value
    ]
