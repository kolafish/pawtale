from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirStoryRevisionChangeType,
)
from app.models.memoir_narrative import (
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.services.memoir.narrative import draft_blocks


def _datetime_value(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def story_snapshot(db: AsyncSession, story: MemoirStory) -> dict[str, object]:
    claim_links = list(
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
    return {
        "title": story.title,
        "story_type": story.story_type,
        "summary": story.summary,
        "time_start": _datetime_value(story.time_start),
        "time_end": _datetime_value(story.time_end),
        "time_precision": story.time_precision,
        "primary_place_entity_id": story.primary_place_entity_id,
        "lifecycle_status": story.lifecycle_status,
        "verification_status": story.verification_status,
        "publication_status": story.publication_status,
        "merged_into_story_id": story.merged_into_story_id,
        "claims": [
            {
                "claim_id": link.claim_id,
                "narrative_role": link.narrative_role,
                "sort_order": link.sort_order,
                "is_core": link.is_core,
            }
            for link in claim_links
        ],
    }


async def create_initial_story_revision(
    db: AsyncSession,
    *,
    story: MemoirStory,
    change_type: MemoirStoryRevisionChangeType,
    actor_type: MemoirNarrativeActorType,
    actor_user_id: str | None,
    reason: str,
) -> MemoirStoryRevision:
    revision = MemoirStoryRevision(
        project_id=story.project_id,
        story_id=story.id,
        story_version=story.version,
        change_type=change_type.value,
        before_state_json={},
        after_state_json=await story_snapshot(db, story),
        reason=reason,
        created_by=actor_type.value,
        created_by_user_id=actor_user_id,
    )
    db.add(revision)
    await db.flush()
    await draft_blocks.mark_blocks_stale_for_story_revision(
        db,
        project_id=story.project_id,
        story_id=story.id,
        current_story_revision_id=revision.id,
    )
    return revision


async def record_story_revision(
    db: AsyncSession,
    *,
    story: MemoirStory,
    before_state: dict[str, object],
    change_type: MemoirStoryRevisionChangeType,
    actor_type: MemoirNarrativeActorType,
    actor_user_id: str | None,
    reason: str,
) -> MemoirStoryRevision:
    expected_version = story.version
    result = await db.execute(
        update(MemoirStory)
        .where(
            MemoirStory.project_id == story.project_id,
            MemoirStory.id == story.id,
            MemoirStory.version == expected_version,
        )
        .values(
            version=expected_version + 1,
            updated_by=actor_type.value,
            updated_by_user_id=actor_user_id,
            updated_at=_utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ValueError("story changed concurrently; retry the operation")
    await db.refresh(story)
    revision = MemoirStoryRevision(
        project_id=story.project_id,
        story_id=story.id,
        story_version=story.version,
        change_type=change_type.value,
        before_state_json=before_state,
        after_state_json=await story_snapshot(db, story),
        reason=reason,
        created_by=actor_type.value,
        created_by_user_id=actor_user_id,
    )
    db.add(revision)
    await db.flush()
    await draft_blocks.mark_blocks_stale_for_story_revision(
        db,
        project_id=story.project_id,
        story_id=story.id,
        current_story_revision_id=revision.id,
    )
    return revision
