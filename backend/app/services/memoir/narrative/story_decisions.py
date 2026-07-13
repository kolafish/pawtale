from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryRevisionChangeType,
    MemoirStorySlotStatus,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirNarrativeDecision,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStorySlot,
)
from app.schemas.memoir_narrative.story_slots import story_slot_schema
from app.services.memoir.narrative import (
    chapter_nodes,
    decision_log,
    story_revisions,
    story_slots,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def story_state(story: MemoirStory) -> dict[str, object]:
    return {
        "lifecycle_status": story.lifecycle_status,
        "completion_status": story.completion_status,
        "verification_status": story.verification_status,
        "publication_status": story.publication_status,
        "merged_into_story_id": story.merged_into_story_id,
        "version": story.version,
    }


async def confirmable_story_ids(
    db: AsyncSession,
    *,
    project_id: str,
    story_ids: Collection[str],
) -> set[str]:
    if not story_ids:
        return set()
    return set(
        (
            await db.scalars(
                select(MemoirStoryClaim.story_id)
                .join(MemoirClaim, MemoirClaim.id == MemoirStoryClaim.claim_id)
                .join(
                    MemoirClaimEvidence, MemoirClaimEvidence.claim_id == MemoirClaim.id
                )
                .where(
                    MemoirStoryClaim.project_id == project_id,
                    MemoirStoryClaim.story_id.in_(story_ids),
                    MemoirStoryClaim.is_core.is_(True),
                    MemoirClaim.project_id == project_id,
                    MemoirClaim.superseded_by_claim_id.is_(None),
                    MemoirClaim.verification_status
                    != MemoirClaimVerificationStatus.REJECTED.value,
                    MemoirClaim.publication_status
                    == MemoirClaimPublicationStatus.ELIGIBLE.value,
                    MemoirClaimEvidence.project_id == project_id,
                    MemoirClaimEvidence.evidence_role
                    == MemoirClaimEvidenceRole.SUPPORTS.value,
                )
                .distinct()
            )
        ).all()
    )


def _slot_state(slot: MemoirStorySlot) -> dict[str, object]:
    return {
        "status": slot.status,
        "assessment_summary": slot.assessment_summary,
        "user_resolution_note": slot.user_resolution_note,
        "resolution_actor_type": slot.resolution_actor_type,
        "resolved_by_user_id": slot.resolved_by_user_id,
        "resolved_at": slot.resolved_at.isoformat() if slot.resolved_at else None,
    }


async def mark_story_needs_review(
    db: AsyncSession,
    *,
    story: MemoirStory,
    candidate_story_ids: tuple[str, ...],
    reason: str,
    idempotency_key: str,
) -> MemoirNarrativeDecision:
    existing = await decision_log.existing_decision(
        db,
        project_id=story.project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.STORY,
        target_id=story.id,
        action=MemoirNarrativeDecisionAction.NEEDS_REVIEW,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
    )
    if existing is not None:
        return existing
    before = story_state(story)
    before_snapshot = await story_revisions.story_snapshot(db, story)
    status_changed = False
    if story.verification_status not in {
        MemoirStoryVerificationStatus.CONFIRMED.value,
        MemoirStoryVerificationStatus.REJECTED.value,
    }:
        status_changed = (
            story.verification_status
            != MemoirStoryVerificationStatus.NEEDS_VERIFICATION.value
        )
        if status_changed:
            story.verification_status = MemoirStoryVerificationStatus.NEEDS_VERIFICATION.value
            story.updated_by = MemoirNarrativeActorType.SYSTEM.value
            story.updated_by_user_id = None
            db.add(story)
            await db.flush()
            await story_revisions.record_story_revision(
                db,
                story=story,
                before_state=before_snapshot,
                change_type=MemoirStoryRevisionChangeType.NEEDS_REVIEW,
                actor_type=MemoirNarrativeActorType.SYSTEM,
                actor_user_id=None,
                reason=reason,
            )
    return await decision_log.record_decision(
        db,
        project_id=story.project_id,
        target_type=MemoirNarrativeDecisionTargetType.STORY,
        target_id=story.id,
        action=MemoirNarrativeDecisionAction.NEEDS_REVIEW,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
        before_state=before,
        after_state={
            **story_state(story),
            "candidate_story_ids": list(candidate_story_ids),
        },
        reason=reason,
        idempotency_key=idempotency_key,
    )


async def decide_story_verification(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
    confirmed: bool,
    actor_user_id: str,
    idempotency_key: str,
    reason: str = "",
) -> MemoirNarrativeDecision:
    action = MemoirNarrativeDecisionAction.CONFIRM if confirmed else MemoirNarrativeDecisionAction.REJECT
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.STORY,
        target_id=story_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        return existing
    story = await db.scalar(
        select(MemoirStory).where(
            MemoirStory.project_id == project_id,
            MemoirStory.id == story_id,
        )
    )
    if story is None:
        raise ValueError("story does not belong to the project")
    if story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value:
        raise ValueError("only an active story can be confirmed or rejected")
    if confirmed:
        confirmable_ids = await confirmable_story_ids(
            db,
            project_id=project_id,
            story_ids={story_id},
        )
        if story_id not in confirmable_ids:
            raise ValueError(
                "story requires a core claim with supporting evidence before confirmation"
            )
    before_snapshot = await story_revisions.story_snapshot(db, story)
    before = story_state(story)
    story.verification_status = (
        MemoirStoryVerificationStatus.CONFIRMED.value
        if confirmed
        else MemoirStoryVerificationStatus.REJECTED.value
    )
    story.verification_decided_by_user_id = actor_user_id
    story.updated_by = MemoirNarrativeActorType.USER.value
    story.updated_by_user_id = actor_user_id
    db.add(story)
    await db.flush()
    await story_revisions.record_story_revision(
        db,
        story=story,
        before_state=before_snapshot,
        change_type=(
            MemoirStoryRevisionChangeType.CONFIRMED
            if confirmed
            else MemoirStoryRevisionChangeType.REJECTED
        ),
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await chapter_nodes.recompute_nodes_for_story(
        db,
        project_id=project_id,
        story_id=story.id,
        actor_user_id=actor_user_id,
    )
    return await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.STORY,
        target_id=story_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=story_state(story),
        reason=reason,
        idempotency_key=idempotency_key,
    )


async def decide_story_publication(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
    publication_status: MemoirStoryPublicationStatus,
    actor_user_id: str,
    idempotency_key: str,
    reason: str = "",
) -> MemoirNarrativeDecision:
    if publication_status == MemoirStoryPublicationStatus.ELIGIBLE:
        raise ValueError("eligible restoration requires a separate review decision")
    action = (
        MemoirNarrativeDecisionAction.MARK_PRIVATE
        if publication_status == MemoirStoryPublicationStatus.PRIVATE
        else MemoirNarrativeDecisionAction.EXCLUDE
    )
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.STORY,
        target_id=story_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        return existing
    story = await db.scalar(
        select(MemoirStory).where(
            MemoirStory.project_id == project_id,
            MemoirStory.id == story_id,
        )
    )
    if story is None:
        raise ValueError("story does not belong to the project")
    if story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value:
        raise ValueError("only an active story publication can be changed")
    before_snapshot = await story_revisions.story_snapshot(db, story)
    before = story_state(story)
    story.publication_status = publication_status.value
    story.publication_decided_by_user_id = actor_user_id
    story.updated_by = MemoirNarrativeActorType.USER.value
    story.updated_by_user_id = actor_user_id
    db.add(story)
    await db.flush()
    await story_revisions.record_story_revision(
        db,
        story=story,
        before_state=before_snapshot,
        change_type=MemoirStoryRevisionChangeType.PUBLICATION_CHANGED,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await chapter_nodes.recompute_nodes_for_story(
        db,
        project_id=project_id,
        story_id=story.id,
        actor_user_id=actor_user_id,
    )
    return await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.STORY,
        target_id=story_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=story_state(story),
        reason=reason,
        idempotency_key=idempotency_key,
    )


async def resolve_story_slot(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
    slot_key: str,
    status: MemoirStorySlotStatus,
    actor_user_id: str,
    idempotency_key: str,
    note: str = "",
) -> MemoirNarrativeDecision:
    if status not in {
        MemoirStorySlotStatus.ACCEPTED_INCOMPLETE,
        MemoirStorySlotStatus.NOT_APPLICABLE,
    }:
        raise ValueError("unsupported manual story slot resolution")
    action = (
        MemoirNarrativeDecisionAction.ACCEPT_INCOMPLETE
        if status == MemoirStorySlotStatus.ACCEPTED_INCOMPLETE
        else MemoirNarrativeDecisionAction.RESOLVE
    )
    story = await db.scalar(
        select(MemoirStory).where(
            MemoirStory.project_id == project_id,
            MemoirStory.id == story_id,
        )
    )
    if story is None:
        raise ValueError("story does not belong to the project")
    if story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value:
        raise ValueError("only an active story slot can be resolved")
    if slot_key not in story_slot_schema(story.story_type, story.slot_schema_version).slot_keys:
        raise ValueError("story slot does not belong to the story schema")
    slot = await db.scalar(
        select(MemoirStorySlot).where(
            MemoirStorySlot.project_id == project_id,
            MemoirStorySlot.story_id == story_id,
            MemoirStorySlot.schema_version == story.slot_schema_version,
            MemoirStorySlot.slot_key == slot_key,
        )
    )
    if slot is None:
        raise ValueError("story slot does not belong to the project")
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.STORY_SLOT,
        target_id=slot.id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        return existing
    if slot.status == MemoirStorySlotStatus.SUFFICIENT.value:
        raise ValueError("sufficient story slot cannot be resolved as incomplete")
    before_story = await story_revisions.story_snapshot(db, story)
    before_slot = _slot_state(slot)
    slot.status = status.value
    slot.user_resolution_note = note.strip()
    slot.resolution_actor_type = MemoirNarrativeActorType.USER.value
    slot.resolved_by_user_id = actor_user_id
    slot.resolved_at = _utcnow()
    db.add(slot)
    await db.flush()
    await story_slots.recompute_story_slots(db, story=story)
    await chapter_nodes.recompute_nodes_for_story(
        db,
        project_id=project_id,
        story_id=story.id,
        actor_user_id=actor_user_id,
    )
    await story_revisions.record_story_revision(
        db,
        story=story,
        before_state=before_story,
        change_type=MemoirStoryRevisionChangeType.SLOT_RESOLVED,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        reason=note,
    )
    return await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.STORY_SLOT,
        target_id=slot.id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before_slot,
        after_state=_slot_state(slot),
        reason=note,
        idempotency_key=idempotency_key,
    )
