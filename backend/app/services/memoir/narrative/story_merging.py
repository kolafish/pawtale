from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
    MemoirStoryLifecycleStatus,
    MemoirStoryRevisionChangeType,
    MemoirStorySlotStatus,
)
from app.models.memoir_narrative import (
    MemoirNarrativeDecision,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStorySlotClaim,
)
from app.services.memoir.narrative import (
    chapter_nodes,
    decision_log,
    story_revisions,
    story_slots,
)
from app.services.memoir.narrative.story_decisions import story_state


async def merge_stories(
    db: AsyncSession,
    *,
    project_id: str,
    source_story_id: str,
    retained_story_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str = "",
) -> MemoirNarrativeDecision:
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.STORY,
        target_id=source_story_id,
        action=MemoirNarrativeDecisionAction.MERGE,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        return existing
    if source_story_id == retained_story_id:
        raise ValueError("story cannot be merged into itself")
    loaded = list(
        (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.id.in_([source_story_id, retained_story_id]),
                )
            )
        ).all()
    )
    by_id = {story.id: story for story in loaded}
    source = by_id.get(source_story_id)
    retained = by_id.get(retained_story_id)
    if source is None or retained is None:
        raise ValueError("merge stories must belong to the same project")
    if (
        source.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value
        or retained.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value
    ):
        raise ValueError("only active stories can be merged")
    if source.story_type != retained.story_type:
        raise ValueError("only stories of the same type can be merged")
    if source.slot_schema_version != retained.slot_schema_version:
        raise ValueError("story slot schemas must match before merge")

    source_before = await story_revisions.story_snapshot(db, source)
    retained_before = await story_revisions.story_snapshot(db, retained)
    source_slots = await story_slots.ensure_story_slots(db, story=source)
    retained_slots = await story_slots.ensure_story_slots(db, story=retained)
    source_slots_by_key = {slot.slot_key: slot for slot in source_slots}
    retained_slots_by_key = {slot.slot_key: slot for slot in retained_slots}
    for slot_key, source_slot in source_slots_by_key.items():
        if source_slot.status not in story_slots.MANUAL_SLOT_STATUSES:
            continue
        retained_slot = retained_slots_by_key[slot_key]
        if retained_slot.status == MemoirStorySlotStatus.SUFFICIENT.value:
            continue
        if retained_slot.status in story_slots.MANUAL_SLOT_STATUSES:
            if (
                retained_slot.status != source_slot.status
                or retained_slot.user_resolution_note != source_slot.user_resolution_note
            ):
                raise ValueError("story merge has conflicting manual slot resolutions")
            continue
        retained_slot.status = source_slot.status
        retained_slot.user_resolution_note = source_slot.user_resolution_note
        retained_slot.resolution_actor_type = source_slot.resolution_actor_type
        retained_slot.resolved_by_user_id = source_slot.resolved_by_user_id
        retained_slot.resolved_at = source_slot.resolved_at
        db.add(retained_slot)

    source_claims = list(
        (
            await db.scalars(
                select(MemoirStoryClaim)
                .where(
                    MemoirStoryClaim.project_id == project_id,
                    MemoirStoryClaim.story_id == source.id,
                )
                .order_by(MemoirStoryClaim.sort_order, MemoirStoryClaim.id)
            )
        ).all()
    )
    retained_claims = list(
        (
            await db.scalars(
                select(MemoirStoryClaim).where(
                    MemoirStoryClaim.project_id == project_id,
                    MemoirStoryClaim.story_id == retained.id,
                )
            )
        ).all()
    )
    retained_claims_by_id = {link.claim_id: link for link in retained_claims}
    next_sort_order = max((link.sort_order for link in retained_claims), default=-1) + 1
    for link in source_claims:
        retained_link = retained_claims_by_id.get(link.claim_id)
        if retained_link is not None:
            if link.is_core and not retained_link.is_core:
                retained_link.is_core = True
                db.add(retained_link)
            continue
        retained_link = MemoirStoryClaim(
            project_id=project_id,
            story_id=retained.id,
            claim_id=link.claim_id,
            narrative_role=link.narrative_role,
            sort_order=next_sort_order,
            is_core=link.is_core,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=actor_user_id,
        )
        db.add(retained_link)
        retained_claims_by_id[link.claim_id] = retained_link
        next_sort_order += 1

    source_slot_ids = [slot.id for slot in source_slots]
    source_slot_claims = list(
        (
            await db.scalars(
                select(MemoirStorySlotClaim).where(
                    MemoirStorySlotClaim.project_id == project_id,
                    MemoirStorySlotClaim.story_slot_id.in_(source_slot_ids),
                )
            )
        ).all()
    )
    source_slot_key_by_id = {slot.id: slot.slot_key for slot in source_slots}
    retained_slot_ids = [slot.id for slot in retained_slots]
    retained_slot_relations = set(
        (
            await db.execute(
                select(
                    MemoirStorySlotClaim.story_slot_id,
                    MemoirStorySlotClaim.claim_id,
                    MemoirStorySlotClaim.evidence_role,
                ).where(
                    MemoirStorySlotClaim.project_id == project_id,
                    MemoirStorySlotClaim.story_slot_id.in_(retained_slot_ids),
                )
            )
        ).all()
    )
    for link in source_slot_claims:
        retained_slot = retained_slots_by_key[source_slot_key_by_id[link.story_slot_id]]
        relation = (retained_slot.id, link.claim_id, link.evidence_role)
        if relation in retained_slot_relations:
            continue
        db.add(
            MemoirStorySlotClaim(
                project_id=project_id,
                story_slot_id=retained_slot.id,
                claim_id=link.claim_id,
                evidence_role=link.evidence_role,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=actor_user_id,
            )
        )
        retained_slot_relations.add(relation)

    source.lifecycle_status = MemoirStoryLifecycleStatus.MERGED.value
    source.merged_into_story_id = retained.id
    source.updated_by = MemoirNarrativeActorType.USER.value
    source.updated_by_user_id = actor_user_id
    db.add(source)
    await db.flush()
    await story_slots.recompute_story_slots(db, story=retained)
    for story in (source, retained):
        await chapter_nodes.recompute_nodes_for_story(
            db,
            project_id=project_id,
            story_id=story.id,
            actor_user_id=actor_user_id,
        )
    await story_revisions.record_story_revision(
        db,
        story=source,
        before_state=source_before,
        change_type=MemoirStoryRevisionChangeType.MERGED,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await story_revisions.record_story_revision(
        db,
        story=retained,
        before_state=retained_before,
        change_type=MemoirStoryRevisionChangeType.MERGED,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    return await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.STORY,
        target_id=source.id,
        action=MemoirNarrativeDecisionAction.MERGE,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state={"source": source_before, "retained": retained_before},
        after_state={"source": story_state(source), "retained": story_state(retained)},
        reason=reason,
        idempotency_key=idempotency_key,
    )
