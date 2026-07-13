from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
    MemoirStoryCompletionStatus,
    MemoirStorySlotStatus,
    MemoirStoryType,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStorySlot,
    MemoirStorySlotClaim,
)
from app.schemas.memoir_narrative.story_slots import story_slot_schema


MANUAL_SLOT_STATUSES = {
    MemoirStorySlotStatus.ACCEPTED_INCOMPLETE.value,
    MemoirStorySlotStatus.NOT_APPLICABLE.value,
}


@dataclass(frozen=True)
class StorySlotRecomputeResult:
    changed_slot_ids: tuple[str, ...]
    completion_changed: bool

    @property
    def changed(self) -> bool:
        return bool(self.changed_slot_ids or self.completion_changed)


async def ensure_story_slots(db: AsyncSession, *, story: MemoirStory) -> list[MemoirStorySlot]:
    schema = story_slot_schema(MemoirStoryType(story.story_type), story.slot_schema_version)
    existing = list(
        (
            await db.scalars(
                select(MemoirStorySlot).where(
                    MemoirStorySlot.project_id == story.project_id,
                    MemoirStorySlot.story_id == story.id,
                )
            )
        ).all()
    )
    unexpected = {
        (slot.schema_version, slot.slot_key)
        for slot in existing
        if slot.schema_version != schema.version or slot.slot_key not in schema.slot_keys
    }
    if unexpected:
        raise ValueError("story slots require an explicit schema migration")
    by_key = {slot.slot_key: slot for slot in existing}
    for definition in schema.slots:
        if definition.key in by_key:
            continue
        slot = MemoirStorySlot(
            project_id=story.project_id,
            story_id=story.id,
            schema_version=schema.version,
            slot_key=definition.key,
            status=MemoirStorySlotStatus.MISSING.value,
        )
        db.add(slot)
        by_key[definition.key] = slot
    await db.flush()
    return [by_key[definition.key] for definition in schema.slots]


def _slot_status(
    *,
    supported_claim_ids: set[str],
    confirmed_claim_ids: set[str],
    contradicting_claim_ids: set[str],
) -> MemoirStorySlotStatus:
    if not supported_claim_ids and not contradicting_claim_ids:
        return MemoirStorySlotStatus.MISSING
    if contradicting_claim_ids:
        return MemoirStorySlotStatus.PARTIAL
    if supported_claim_ids & confirmed_claim_ids:
        return MemoirStorySlotStatus.SUFFICIENT
    return MemoirStorySlotStatus.PARTIAL


async def recompute_story_slots(
    db: AsyncSession,
    *,
    story: MemoirStory,
) -> StorySlotRecomputeResult:
    slots = await ensure_story_slots(db, story=story)
    slot_ids = [slot.id for slot in slots]
    links = []
    if slot_ids:
        links = list(
            (
                await db.scalars(
                    select(MemoirStorySlotClaim).where(
                        MemoirStorySlotClaim.project_id == story.project_id,
                        MemoirStorySlotClaim.story_slot_id.in_(slot_ids),
                    )
                )
            ).all()
        )
    claim_ids = {link.claim_id for link in links}
    claims = []
    evidence_claim_ids: set[str] = set()
    if claim_ids:
        claims = list(
            (
                await db.scalars(
                    select(MemoirClaim).where(
                        MemoirClaim.project_id == story.project_id,
                        MemoirClaim.id.in_(claim_ids),
                    )
                )
            ).all()
        )
        evidence_claim_ids = set(
            (
                await db.scalars(
                    select(MemoirClaimEvidence.claim_id)
                    .where(
                        MemoirClaimEvidence.project_id == story.project_id,
                        MemoirClaimEvidence.claim_id.in_(claim_ids),
                        MemoirClaimEvidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value,
                    )
                    .distinct()
                )
            ).all()
        )
    valid_claims = {
        claim.id: claim
        for claim in claims
        if claim.id in evidence_claim_ids
        and claim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value
        and claim.superseded_by_claim_id is None
        and claim.verification_status != MemoirClaimVerificationStatus.REJECTED.value
    }
    confirmed_claim_ids = {
        claim_id
        for claim_id, claim in valid_claims.items()
        if claim.verification_status == MemoirClaimVerificationStatus.CONFIRMED.value
    }
    links_by_slot: dict[str, list[MemoirStorySlotClaim]] = {}
    for link in links:
        if link.claim_id in valid_claims:
            links_by_slot.setdefault(link.story_slot_id, []).append(link)

    changed_slot_ids: list[str] = []
    for slot in slots:
        slot_links = links_by_slot.get(slot.id, [])
        supported = {
            link.claim_id
            for link in slot_links
            if link.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value
        }
        contradicting = {
            link.claim_id
            for link in slot_links
            if link.evidence_role == MemoirClaimEvidenceRole.CONTRADICTS.value
        }
        assessed = _slot_status(
            supported_claim_ids=supported,
            confirmed_claim_ids=confirmed_claim_ids,
            contradicting_claim_ids=contradicting,
        )
        summary = (
            f"supports={len(supported)}; "
            f"confirmed={len(supported & confirmed_claim_ids)}; "
            f"contradicts={len(contradicting)}"
        )
        new_status = slot.status if slot.status in MANUAL_SLOT_STATUSES else assessed.value
        if slot.status != new_status or slot.assessment_summary != summary:
            slot.status = new_status
            slot.assessment_summary = summary
            db.add(slot)
            changed_slot_ids.append(slot.id)

    schema = story_slot_schema(MemoirStoryType(story.story_type), story.slot_schema_version)
    statuses = {slot.slot_key: slot.status for slot in slots}
    required_statuses = [statuses[key] for key in schema.required_slot_keys]
    if any(
        status in {MemoirStorySlotStatus.MISSING.value, MemoirStorySlotStatus.PARTIAL.value}
        for status in required_statuses
    ):
        completion = MemoirStoryCompletionStatus.INCOMPLETE
    elif MemoirStorySlotStatus.ACCEPTED_INCOMPLETE.value in required_statuses:
        completion = MemoirStoryCompletionStatus.ACCEPTED_INCOMPLETE
    else:
        completion = MemoirStoryCompletionStatus.SUFFICIENT
    completion_changed = story.completion_status != completion.value
    if completion_changed:
        story.completion_status = completion.value
        story.updated_by = MemoirNarrativeActorType.SYSTEM.value
        story.updated_by_user_id = None
        db.add(story)
    await db.flush()
    return StorySlotRecomputeResult(
        changed_slot_ids=tuple(changed_slot_ids),
        completion_changed=completion_changed,
    )
