from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterNodeStatus,
    MemoirChapterNodeType,
    MemoirNarrativeActorType,
)
from app.models.memoir_narrative import (
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirStory,
)
from app.services.memoir.narrative import chapter_contracts
from app.services.memoir.narrative.readiness_policy import story_can_support_draft


async def _story_links_for_node(
    db: AsyncSession,
    *,
    node: MemoirChapterNode,
) -> list[MemoirChapterNodeStory]:
    return list(
        (
            await db.scalars(
                select(MemoirChapterNodeStory)
                .where(
                    MemoirChapterNodeStory.project_id == node.project_id,
                    MemoirChapterNodeStory.node_id == node.id,
                )
                .order_by(
                    MemoirChapterNodeStory.sort_order,
                    MemoirChapterNodeStory.id,
                )
            )
        ).all()
    )


async def recompute_story_node_status(
    db: AsyncSession,
    *,
    node: MemoirChapterNode,
    actor_user_id: str | None,
) -> None:
    if (
        node.node_type != MemoirChapterNodeType.STORY.value
        or node.status == MemoirChapterNodeStatus.EXCLUDED.value
    ):
        return
    links = await _story_links_for_node(db, node=node)
    required_links = [link for link in links if link.required] or links
    story_ids = {link.story_id for link in required_links}
    stories = (
        {
            story.id: story
            for story in (
                await db.scalars(
                    select(MemoirStory).where(
                        MemoirStory.project_id == node.project_id,
                        MemoirStory.id.in_(story_ids),
                    )
                )
            ).all()
        }
        if story_ids
        else {}
    )
    supported_story_ids = {
        story_id
        for story_id, _, _ in await chapter_contracts.eligible_story_claim_shapes(
            db,
            project_id=node.project_id,
            story_ids=story_ids,
        )
    }
    ready = bool(required_links) and all(
        story_id in stories
        and story_id in supported_story_ids
        and story_can_support_draft(stories[story_id])
        for story_id in story_ids
    )
    status = (
        MemoirChapterNodeStatus.READY.value
        if ready
        else MemoirChapterNodeStatus.NEEDS_WORK.value
    )
    if node.status == status:
        return
    node.status = status
    node.updated_by = (
        MemoirNarrativeActorType.USER.value
        if actor_user_id
        else MemoirNarrativeActorType.SYSTEM.value
    )
    node.updated_by_user_id = actor_user_id
    db.add(node)
    await db.flush()
