from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
)
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirDraftBlock,
    MemoirDraftBlockClaim,
    MemoirDraftBlockStory,
    MemoirDraftBlockVersion,
    MemoirNarrativeEntity,
    MemoirNarrativeDecision,
)
from app.services.memoir.narrative.gap_rules import (
    ChapterAuditContext,
    load_chapter_audit_context,
)


BOOK_CONSISTENCY_ENGINE_VERSION = "1"


@dataclass(frozen=True)
class BookDraftBlockContext:
    block: MemoirDraftBlock
    version: MemoirDraftBlockVersion
    claim_ids: tuple[str, ...]
    story_ids: tuple[str, ...]
    locked_version_id: str | None


@dataclass(frozen=True)
class BookConsistencyContext:
    plan: MemoirBookPlan | None
    chapters: tuple[ChapterAuditContext, ...]
    blocks: tuple[BookDraftBlockContext, ...]
    output: MemoirBookOutput | None
    output_version: MemoirBookOutputVersion | None
    claims: dict[str, MemoirClaim]
    evidence_counts: dict[str, int]
    entities: dict[str, MemoirNarrativeEntity]

    @property
    def project_id(self) -> str:
        if self.plan is not None:
            return self.plan.project_id
        if self.output is not None:
            return self.output.project_id
        return ""


def _latest_output_version(output: MemoirBookOutput | None) -> MemoirBookOutputVersion | None:
    if output is None or not output.versions:
        return None
    return max(output.versions, key=lambda item: (item.version, item.created_at, item.id))


async def load_book_consistency_context(
    db: AsyncSession,
    *,
    project_id: str,
) -> BookConsistencyContext:
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(selectinload(MemoirBookPlan.chapters))
    )
    chapter_contexts: list[ChapterAuditContext] = []
    if plan is not None:
        for chapter in sorted(plan.chapters, key=lambda item: (item.sort_order, item.id)):
            chapter_contexts.append(
                await load_chapter_audit_context(
                    db,
                    project_id=project_id,
                    chapter_id=chapter.id,
                )
            )

    current_node_ids = {
        node.id
        for chapter in chapter_contexts
        for node in chapter.nodes
    }
    block_rows = []
    if current_node_ids:
        block_rows = (
            await db.execute(
                select(MemoirDraftBlock, MemoirDraftBlockVersion)
                .join(
                    MemoirDraftBlockVersion,
                    MemoirDraftBlockVersion.id == MemoirDraftBlock.current_version_id,
                )
                .where(
                    MemoirDraftBlock.project_id == project_id,
                    MemoirDraftBlock.node_id.in_(current_node_ids),
                )
                .order_by(
                    MemoirDraftBlock.chapter_id,
                    MemoirDraftBlock.sort_order,
                    MemoirDraftBlock.id,
                )
            )
        ).all()
    version_ids = [version.id for _, version in block_rows]
    block_ids = [block.id for block, _ in block_rows]
    claim_ids_by_version: dict[str, list[str]] = {}
    story_ids_by_version: dict[str, list[str]] = {}
    if version_ids:
        for link in (
            await db.scalars(
                select(MemoirDraftBlockClaim)
                .where(
                    MemoirDraftBlockClaim.project_id == project_id,
                    MemoirDraftBlockClaim.block_version_id.in_(version_ids),
                )
                .order_by(
                    MemoirDraftBlockClaim.block_version_id,
                    MemoirDraftBlockClaim.claim_id,
                )
            )
        ).all():
            claim_ids_by_version.setdefault(link.block_version_id, []).append(link.claim_id)
        for link in (
            await db.scalars(
                select(MemoirDraftBlockStory)
                .where(
                    MemoirDraftBlockStory.project_id == project_id,
                    MemoirDraftBlockStory.block_version_id.in_(version_ids),
                )
                .order_by(
                    MemoirDraftBlockStory.block_version_id,
                    MemoirDraftBlockStory.story_id,
                )
            )
        ).all():
            story_ids_by_version.setdefault(link.block_version_id, []).append(link.story_id)
    locked_version_ids: dict[str, str] = {}
    if block_ids:
        lock_decisions = (
            await db.scalars(
                select(MemoirNarrativeDecision)
                .where(
                    MemoirNarrativeDecision.project_id == project_id,
                    MemoirNarrativeDecision.target_type
                    == MemoirNarrativeDecisionTargetType.DRAFT_BLOCK.value,
                    MemoirNarrativeDecision.target_id.in_(block_ids),
                    MemoirNarrativeDecision.action
                    == MemoirNarrativeDecisionAction.LOCK.value,
                )
                .order_by(
                    MemoirNarrativeDecision.target_id,
                    MemoirNarrativeDecision.created_at.desc(),
                    MemoirNarrativeDecision.id.desc(),
                )
            )
        ).all()
        for decision in lock_decisions:
            if decision.target_id in locked_version_ids:
                continue
            state = decision.after_state_json if isinstance(decision.after_state_json, dict) else {}
            version_id = str(state.get("current_version_id") or "").strip()
            if version_id:
                locked_version_ids[decision.target_id] = version_id
    block_contexts = tuple(
        BookDraftBlockContext(
            block=block,
            version=version,
            claim_ids=tuple(claim_ids_by_version.get(version.id, [])),
            story_ids=tuple(story_ids_by_version.get(version.id, [])),
            locked_version_id=locked_version_ids.get(block.id),
        )
        for block, version in block_rows
    )

    claims = {
        claim.id: claim
        for chapter in chapter_contexts
        for claim in chapter.claims.values()
    }
    block_claim_ids = {
        claim_id
        for block in block_contexts
        for claim_id in block.claim_ids
        if claim_id not in claims
    }
    if block_claim_ids:
        claims.update(
            {
                claim.id: claim
                for claim in (
                    await db.scalars(
                        select(MemoirClaim).where(
                            MemoirClaim.project_id == project_id,
                            MemoirClaim.id.in_(block_claim_ids),
                        )
                    )
                ).all()
            }
        )
    claim_ids = set(claims)
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
    entity_ids = {
        entity_id
        for claim in claims.values()
        for entity_id in (
            claim.subject_entity_id,
            claim.object_entity_id,
            claim.place_entity_id,
        )
        if entity_id
    }
    entities = {
        entity.id: entity
        for entity in (
            await db.scalars(
                select(MemoirNarrativeEntity).where(
                    MemoirNarrativeEntity.project_id == project_id,
                    MemoirNarrativeEntity.id.in_(entity_ids),
                )
            )
        ).all()
    } if entity_ids else {}
    output = await db.scalar(
        select(MemoirBookOutput)
        .where(MemoirBookOutput.project_id == project_id)
        .options(selectinload(MemoirBookOutput.versions))
    )
    return BookConsistencyContext(
        plan=plan,
        chapters=tuple(chapter_contexts),
        blocks=block_contexts,
        output=output,
        output_version=_latest_output_version(output),
        claims=claims,
        evidence_counts=evidence_counts,
        entities=entities,
    )


def book_consistency_signature(context: BookConsistencyContext) -> str:
    payload = {
        "engine_version": BOOK_CONSISTENCY_ENGINE_VERSION,
        "plan": (
            [context.plan.id, context.plan.status, context.plan.updated_at.isoformat()]
            if context.plan
            else None
        ),
        "chapters": [
            {
                "chapter": [
                    chapter.chapter.id,
                    chapter.chapter.sort_order,
                    chapter.chapter.updated_at.isoformat(),
                ],
                "contract": (
                    [
                        chapter.contract.id,
                        chapter.contract.version,
                        chapter.contract.status,
                        chapter.contract.updated_at.isoformat(),
                    ]
                    if chapter.contract
                    else None
                ),
                "nodes": [
                    [node.id, node.sort_order, node.required, node.status, node.updated_at.isoformat()]
                    for node in chapter.nodes
                ],
                "links": [
                    [link.node_id, link.story_id, link.story_role, link.required, link.sort_order]
                    for link in chapter.links
                ],
                "stories": [
                    [story.id, story.version, story.updated_at.isoformat()]
                    for story in sorted(chapter.stories.values(), key=lambda item: item.id)
                ],
                "slots": [
                    [slot.id, slot.story_id, slot.slot_key, slot.status, slot.updated_at.isoformat()]
                    for slots in chapter.slots.values()
                    for slot in sorted(slots.values(), key=lambda item: item.slot_key)
                ],
            }
            for chapter in context.chapters
        ],
        "claims": [
            [
                claim.id,
                claim.verification_status,
                claim.publication_status,
                claim.superseded_by_claim_id,
                claim.conflict_group_id,
                context.evidence_counts.get(claim.id, 0),
                claim.updated_at.isoformat(),
            ]
            for claim in sorted(context.claims.values(), key=lambda item: item.id)
        ],
        "entities": [
            [
                entity.id,
                entity.entity_type,
                entity.canonical_name,
                entity.verification_status,
                entity.merged_into_entity_id,
                entity.updated_at.isoformat(),
            ]
            for entity in sorted(context.entities.values(), key=lambda item: item.id)
        ],
        "blocks": [
            [
                item.block.id,
                item.block.chapter_id,
                item.block.block_role,
                item.block.editorial_status,
                item.block.freshness_status,
                item.block.locked,
                item.locked_version_id,
                item.version.id,
                item.version.created_at.isoformat(),
                list(item.claim_ids),
                list(item.story_ids),
            ]
            for item in context.blocks
        ],
        "output": (
            {
                "id": context.output.id,
                "version_id": context.output_version.id,
                "version": context.output_version.version,
                "content_hash": sha256(context.output_version.content.encode("utf-8")).hexdigest(),
                "chapter_snapshot": context.output_version.chapter_snapshot_json,
                "manuscript_hash": sha256(
                    json.dumps(
                        context.output_version.manuscript_json or {},
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            }
            if context.output and context.output_version
            else None
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()
