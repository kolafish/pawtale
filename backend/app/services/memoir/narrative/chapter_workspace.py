from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.memoir_narrative import MemoirClaim
from app.schemas.memoir_narrative import (
    MemoirChapterCompletionRefreshJobResponse,
    MemoirChapterWorkspaceResponse,
    MemoirChapterWorkspaceStoryResponse,
    MemoirClaimResponse,
)
from app.services.memoir.narrative import (
    chapter_contracts,
    chapter_completion_summary,
)
from app.services.memoir.narrative.gap_rules import load_chapter_audit_context


async def get_chapter_workspace(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterWorkspaceResponse:
    context = await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    claim_ids = set(context.claims)
    claims = {
        claim.id: claim
        for claim in (
            await db.scalars(
                select(MemoirClaim)
                .options(selectinload(MemoirClaim.evidence))
                .where(
                    MemoirClaim.project_id == project_id,
                    MemoirClaim.id.in_(claim_ids),
                )
            )
        ).unique().all()
    } if claim_ids else {}
    links_by_node = {
        node.id: sorted(
            (link for link in context.links if link.node_id == node.id),
            key=lambda link: (link.sort_order, link.id),
        )
        for node in context.nodes
    }
    ordered_story_ids = list(
        dict.fromkeys(
            link.story_id
            for node in context.nodes
            for link in links_by_node[node.id]
            if link.story_id in context.stories
        )
    )
    story_responses: list[MemoirChapterWorkspaceStoryResponse] = []
    for story_id in ordered_story_ids:
        story = context.stories[story_id]
        story_responses.append(
            MemoirChapterWorkspaceStoryResponse(
                id=story.id,
                title=story.title,
                summary=story.summary,
                publication_status=story.publication_status,
                claims=[
                    MemoirClaimResponse.model_validate(claims[link.claim_id])
                    for link in context.story_claims.get(story.id, [])
                    if link.claim_id in claims
                ],
            )
        )

    completion = await chapter_completion_summary.get_chapter_completion_read_model(
        db,
        project_id=project_id,
        chapter=context.chapter,
        context=context,
    )
    return MemoirChapterWorkspaceResponse(
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_title=context.chapter.title,
        completion_summary=completion.summary,
        contract=(
            await chapter_contracts.contract_response(db, contract=context.contract)
            if context.contract
            else None
        ),
        stories=story_responses,
        answer_updates=completion.answer_updates,
        latest_refresh_job=(
            MemoirChapterCompletionRefreshJobResponse.model_validate(
                completion.refresh_job
            )
            if completion.refresh_job
            else None
        ),
    )
