from collections.abc import Collection
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
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirNarrativeDecision,
    MemoirStory,
    MemoirStoryClaim,
)
from app.services.memoir.narrative import decision_log, story_revisions


def story_state(story: MemoirStory) -> dict[str, object]:
    return {
        "lifecycle_status": story.lifecycle_status,
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
