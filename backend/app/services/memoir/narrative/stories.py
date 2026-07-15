from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirStoryLifecycleStatus,
    MemoirStoryRevisionChangeType,
    MemoirStorySlotStatus,
    MemoirStoryType,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
    MemoirStorySlot,
    MemoirStorySlotClaim,
)
from app.repositories.memoir_narrative import MemoirStoryRepository
from app.schemas.memoir_narrative import (
    MemoirStoryClaimInput,
    MemoirStoryClaimLinkResponse,
    MemoirStoryCreate,
    MemoirStoryResponse,
    MemoirStoryRevisionResponse,
    MemoirStorySlotClaimResponse,
    MemoirStorySlotResponse,
    MemoirStorySummaryResponse,
)
from app.schemas.memoir_narrative.story_slots import story_slot_schema
from app.services.memoir.narrative import story_revisions, story_slots


async def create_story(
    db: AsyncSession,
    *,
    data: MemoirStoryCreate,
    story_id: str | None = None,
    change_type: MemoirStoryRevisionChangeType = MemoirStoryRevisionChangeType.CREATED,
    reason: str = "",
) -> MemoirStory:
    story = await MemoirStoryRepository(db).create(data, story_id=story_id)
    await story_revisions.create_initial_story_revision(
        db,
        story=story,
        change_type=change_type,
        actor_type=data.created_by,
        actor_user_id=data.created_by_user_id,
        reason=reason,
    )
    return story


async def recompute_story_with_revision(
    db: AsyncSession,
    *,
    story: MemoirStory,
    reason: str,
) -> bool:
    if story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value:
        return False
    before = await story_revisions.story_snapshot(db, story)
    result = await story_slots.recompute_story_slots(db, story=story)
    if not result.changed:
        return False
    await story_revisions.record_story_revision(
        db,
        story=story,
        before_state=before,
        change_type=MemoirStoryRevisionChangeType.SLOTS_RECOMPUTED,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
        reason=reason,
    )
    return True


async def attach_claims_to_story(
    db: AsyncSession,
    *,
    story: MemoirStory,
    claims: list[MemoirStoryClaimInput],
    actor_type: MemoirNarrativeActorType,
    actor_user_id: str | None,
    reason: str,
) -> bool:
    if story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value:
        raise ValueError("claims can only be attached to an active story")
    if actor_type == MemoirNarrativeActorType.USER and not actor_user_id:
        raise ValueError("user story update requires actor_user_id")
    schema = story_slot_schema(MemoirStoryType(story.story_type), story.slot_schema_version)
    requested_claim_ids = {item.claim_id for item in claims}
    project_claims = {
        claim.id: claim
        for claim in (
            await db.scalars(
                select(MemoirClaim).where(
                    MemoirClaim.project_id == story.project_id,
                    MemoirClaim.id.in_(requested_claim_ids),
                )
            )
        ).all()
    }
    if set(project_claims) != requested_claim_ids:
        raise ValueError("story claims must belong to the same project")
    for item in claims:
        if not set(item.slot_keys).issubset(set(schema.slot_keys)):
            raise ValueError("story claim references a slot outside the story schema")

    before = await story_revisions.story_snapshot(db, story)
    requested_slot_keys = {
        slot_key
        for item in claims
        for slot_key in item.slot_keys
    }
    slots = await story_slots.ensure_story_slot_keys(
        db,
        story=story,
        slot_keys=requested_slot_keys,
    )
    slots_by_key = {slot.slot_key: slot for slot in slots}
    existing_story_claims = {
        link.claim_id: link
        for link in (
            await db.scalars(
                select(MemoirStoryClaim).where(
                    MemoirStoryClaim.project_id == story.project_id,
                    MemoirStoryClaim.story_id == story.id,
                    MemoirStoryClaim.claim_id.in_(requested_claim_ids),
                )
            )
        ).all()
    }
    requested_slot_ids = {
        slots_by_key[slot_key].id
        for item in claims
        for slot_key in item.slot_keys
    }
    existing_slot_claims: set[tuple[str, str, str]] = set()
    if requested_slot_ids:
        existing_slot_claims = {
            (link.story_slot_id, link.claim_id, link.evidence_role)
            for link in (
                await db.scalars(
                    select(MemoirStorySlotClaim).where(
                        MemoirStorySlotClaim.project_id == story.project_id,
                        MemoirStorySlotClaim.story_slot_id.in_(requested_slot_ids),
                        MemoirStorySlotClaim.claim_id.in_(requested_claim_ids),
                    )
                )
            ).all()
        }

    changed = False
    for item in claims:
        if item.claim_id not in existing_story_claims:
            link = MemoirStoryClaim(
                project_id=story.project_id,
                story_id=story.id,
                claim_id=item.claim_id,
                narrative_role=item.narrative_role.value,
                sort_order=item.sort_order,
                is_core=item.is_core,
                created_by=actor_type.value,
                created_by_user_id=actor_user_id,
            )
            db.add(link)
            existing_story_claims[item.claim_id] = link
            changed = True
        for slot_key in item.slot_keys:
            slot = slots_by_key[slot_key]
            relation_key = (slot.id, item.claim_id, item.evidence_role.value)
            if relation_key in existing_slot_claims:
                continue
            db.add(
                MemoirStorySlotClaim(
                    project_id=story.project_id,
                    story_slot_id=slot.id,
                    claim_id=item.claim_id,
                    evidence_role=item.evidence_role.value,
                    created_by=actor_type.value,
                    created_by_user_id=actor_user_id,
                )
            )
            existing_slot_claims.add(relation_key)
            changed = True
    await db.flush()
    recomputed = await story_slots.recompute_story_slots(db, story=story)
    if changed or recomputed.changed:
        await story_revisions.record_story_revision(
            db,
            story=story,
            before_state=before,
            change_type=MemoirStoryRevisionChangeType.CLAIMS_UPDATED,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            reason=reason,
        )
        return True
    return False


async def _story_relations(
    db: AsyncSession,
    *,
    story: MemoirStory,
) -> tuple[list[MemoirStoryClaim], list[MemoirStorySlot], list[MemoirStorySlotClaim], list[MemoirStoryRevision]]:
    claims = list(
        (
            await db.scalars(
                select(MemoirStoryClaim)
                .where(
                    MemoirStoryClaim.project_id == story.project_id,
                    MemoirStoryClaim.story_id == story.id,
                )
                .order_by(MemoirStoryClaim.sort_order, MemoirStoryClaim.id)
            )
        ).all()
    )
    slots = await story_slots.load_story_slots(db, story=story)
    slot_ids = [slot.id for slot in slots]
    slot_claims = []
    if slot_ids:
        slot_claims = list(
            (
                await db.scalars(
                    select(MemoirStorySlotClaim)
                    .where(
                        MemoirStorySlotClaim.project_id == story.project_id,
                        MemoirStorySlotClaim.story_slot_id.in_(slot_ids),
                    )
                    .order_by(MemoirStorySlotClaim.story_slot_id, MemoirStorySlotClaim.id)
                )
            ).all()
        )
    revisions = list(
        (
            await db.scalars(
                select(MemoirStoryRevision)
                .where(
                    MemoirStoryRevision.project_id == story.project_id,
                    MemoirStoryRevision.story_id == story.id,
                )
                .order_by(MemoirStoryRevision.story_version.desc())
            )
        ).all()
    )
    return claims, slots, slot_claims, revisions


async def story_response(db: AsyncSession, *, story: MemoirStory) -> MemoirStoryResponse:
    claims, slots, slot_claims, revisions = await _story_relations(db, story=story)
    schema = story_slot_schema(MemoirStoryType(story.story_type), story.slot_schema_version)
    slots_by_key = {slot.slot_key: slot for slot in slots}
    slot_claims_by_slot: dict[str, list[MemoirStorySlotClaimResponse]] = {}
    for link in slot_claims:
        slot_claims_by_slot.setdefault(link.story_slot_id, []).append(
            MemoirStorySlotClaimResponse.model_validate(link)
        )
    summary = MemoirStorySummaryResponse.model_validate(story).model_dump()
    return MemoirStoryResponse(
        **summary,
        claims=[MemoirStoryClaimLinkResponse.model_validate(link) for link in claims],
        slots=[
            MemoirStorySlotResponse(
                id=slot.id if slot else None,
                schema_version=schema.version,
                slot_key=definition.key,
                required=definition.required,
                status=(
                    slot.status
                    if slot is not None
                    else MemoirStorySlotStatus.MISSING.value
                ),
                assessment_summary=(slot.assessment_summary if slot else ""),
                user_resolution_note=(slot.user_resolution_note if slot else ""),
                resolution_actor_type=(slot.resolution_actor_type if slot else None),
                resolved_by_user_id=(slot.resolved_by_user_id if slot else None),
                resolved_at=(slot.resolved_at if slot else None),
                claims=(slot_claims_by_slot.get(slot.id, []) if slot else []),
                updated_at=(slot.updated_at if slot else None),
            )
            for definition in schema.slots
            for slot in [slots_by_key.get(definition.key)]
        ],
        revisions=[MemoirStoryRevisionResponse.model_validate(revision) for revision in revisions],
    )


async def get_story_response(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
) -> MemoirStoryResponse | None:
    story = await MemoirStoryRepository(db).get(project_id=project_id, story_id=story_id)
    if story is None:
        return None
    return await story_response(db, story=story)


async def list_story_responses(
    db: AsyncSession,
    *,
    project_id: str,
    story_type=None,
    lifecycle_status=None,
    completion_status=None,
    verification_status=None,
    publication_status=None,
    limit: int = 100,
    offset: int = 0,
) -> list[MemoirStorySummaryResponse]:
    items = await MemoirStoryRepository(db).list_for_project(
        project_id=project_id,
        story_type=story_type,
        lifecycle_status=lifecycle_status,
        completion_status=completion_status,
        verification_status=verification_status,
        publication_status=publication_status,
        limit=limit,
        offset=offset,
    )
    return [MemoirStorySummaryResponse.model_validate(story) for story in items]
