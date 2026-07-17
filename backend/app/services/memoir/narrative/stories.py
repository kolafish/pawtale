from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirStoryLifecycleStatus,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirStory,
    MemoirStoryClaim,
)
from app.repositories.memoir_narrative import MemoirStoryRepository
from app.schemas.memoir_narrative import (
    MemoirStoryClaimInput,
    MemoirStoryClaimLinkResponse,
    MemoirStoryCreate,
    MemoirStoryResponse,
    MemoirStorySummaryResponse,
)


async def create_story(
    db: AsyncSession,
    *,
    data: MemoirStoryCreate,
    story_id: str | None = None,
) -> MemoirStory:
    return await MemoirStoryRepository(db).create(data, story_id=story_id)


async def attach_claims_to_story(
    db: AsyncSession,
    *,
    story: MemoirStory,
    claims: list[MemoirStoryClaimInput],
    actor_type: MemoirNarrativeActorType,
    actor_user_id: str | None,
) -> bool:
    if story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value:
        raise ValueError("claims can only be attached to an active story")
    if actor_type == MemoirNarrativeActorType.USER and not actor_user_id:
        raise ValueError("user story update requires actor_user_id")
    requested_claim_ids = {item.claim_id for item in claims}
    project_claim_ids = set(
        (
            await db.scalars(
                select(MemoirClaim.id).where(
                    MemoirClaim.project_id == story.project_id,
                    MemoirClaim.id.in_(requested_claim_ids),
                )
            )
        ).all()
    )
    if project_claim_ids != requested_claim_ids:
        raise ValueError("story claims must belong to the same project")

    existing = {
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
    changed = False
    for item in claims:
        link = existing.get(item.claim_id)
        if link is None:
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
            existing[item.claim_id] = link
            changed = True
            continue
        if (
            link.narrative_role != item.narrative_role.value
            or link.sort_order != item.sort_order
            or link.is_core != item.is_core
        ):
            link.narrative_role = item.narrative_role.value
            link.sort_order = item.sort_order
            link.is_core = item.is_core
            db.add(link)
            changed = True
    if not changed:
        return False
    story.version += 1
    story.updated_by = actor_type.value
    story.updated_by_user_id = actor_user_id
    db.add(story)
    await db.flush()
    return True


async def story_response(
    db: AsyncSession,
    *,
    story: MemoirStory,
) -> MemoirStoryResponse:
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
    return MemoirStoryResponse(
        **MemoirStorySummaryResponse.model_validate(story).model_dump(),
        claims=[
            MemoirStoryClaimLinkResponse.model_validate(link)
            for link in claims
        ],
    )


async def get_story_response(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
) -> MemoirStoryResponse | None:
    story = await MemoirStoryRepository(db).get(
        project_id=project_id,
        story_id=story_id,
    )
    if story is None:
        return None
    return await story_response(db, story=story)


async def list_story_responses(
    db: AsyncSession,
    *,
    project_id: str,
    story_type=None,
    lifecycle_status=None,
    verification_status=None,
    publication_status=None,
    limit: int = 100,
    offset: int = 0,
) -> list[MemoirStorySummaryResponse]:
    items = await MemoirStoryRepository(db).list_for_project(
        project_id=project_id,
        story_type=story_type,
        lifecycle_status=lifecycle_status,
        verification_status=verification_status,
        publication_status=publication_status,
        limit=limit,
        offset=offset,
    )
    return [
        MemoirStorySummaryResponse.model_validate(story)
        for story in items
    ]
