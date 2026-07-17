from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
    MemoirStoryLifecycleStatus,
    MemoirStoryRevisionChangeType,
)
from app.models.memoir_narrative import (
    MemoirNarrativeDecision,
    MemoirStory,
    MemoirStoryClaim,
)
from app.services.memoir.narrative import decision_log, story_revisions
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

    source_before = await story_revisions.story_snapshot(db, source)
    retained_before = await story_revisions.story_snapshot(db, retained)
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
    retained_by_claim = {link.claim_id: link for link in retained_claims}
    next_order = max((link.sort_order for link in retained_claims), default=-1) + 1
    for source_link in source_claims:
        retained_link = retained_by_claim.get(source_link.claim_id)
        if retained_link is not None:
            if source_link.is_core and not retained_link.is_core:
                retained_link.is_core = True
                db.add(retained_link)
            continue
        retained_link = MemoirStoryClaim(
            project_id=project_id,
            story_id=retained.id,
            claim_id=source_link.claim_id,
            narrative_role=source_link.narrative_role,
            sort_order=next_order,
            is_core=source_link.is_core,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=actor_user_id,
        )
        db.add(retained_link)
        retained_by_claim[source_link.claim_id] = retained_link
        next_order += 1

    source.lifecycle_status = MemoirStoryLifecycleStatus.MERGED.value
    source.merged_into_story_id = retained.id
    source.updated_by = MemoirNarrativeActorType.USER.value
    source.updated_by_user_id = actor_user_id
    db.add(source)
    await db.flush()
    for story, before in ((source, source_before), (retained, retained_before)):
        await story_revisions.record_story_revision(
            db,
            story=story,
            before_state=before,
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
