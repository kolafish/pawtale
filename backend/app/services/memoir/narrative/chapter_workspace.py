from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirChapterNodeStoryRole,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStorySlotStatus,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import MemoirClaim, MemoirStory, MemoirStorySlotClaim
from app.schemas.memoir_narrative import (
    MemoirChapterCompletionRefreshJobResponse,
    MemoirChapterWorkspaceAvailableStoryResponse,
    MemoirChapterWorkspaceResponse,
    MemoirChapterWorkspaceStoryResponse,
    MemoirChapterWorkspaceStorySlotResponse,
    MemoirClaimResponse,
)
from app.schemas.memoir_narrative.story_slots import story_slot_schema
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
    slot_ids = {
        slot.id
        for story_slots in context.slots.values()
        for slot in story_slots.values()
    }
    slot_claim_ids: dict[str, list[str]] = {}
    if slot_ids:
        for link in (
            await db.scalars(
                select(MemoirStorySlotClaim)
                .where(
                    MemoirStorySlotClaim.project_id == project_id,
                    MemoirStorySlotClaim.story_slot_id.in_(slot_ids),
                )
                .order_by(MemoirStorySlotClaim.id)
            )
        ).all():
            slot_claim_ids.setdefault(link.story_slot_id, []).append(link.claim_id)

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
        schema = story_slot_schema(story.story_type, story.slot_schema_version)
        persisted_slots = context.slots.get(story.id, {})
        story_responses.append(
            MemoirChapterWorkspaceStoryResponse(
                id=story.id,
                title=story.title,
                story_type=story.story_type,
                summary=story.summary,
                completion_status=story.completion_status,
                verification_status=story.verification_status,
                publication_status=story.publication_status,
                story_version=story.version,
                slots=[
                    MemoirChapterWorkspaceStorySlotResponse(
                        id=(persisted.id if persisted else None),
                        slot_key=definition.key,
                        required=definition.required,
                        status=(
                            persisted.status
                            if persisted
                            else MemoirStorySlotStatus.MISSING.value
                        ),
                        assessment_summary=(
                            persisted.assessment_summary if persisted else ""
                        ),
                        claim_ids=(slot_claim_ids.get(persisted.id, []) if persisted else []),
                    )
                    for definition in schema.slots
                    for persisted in [persisted_slots.get(definition.key)]
                ],
                claims=[
                    MemoirClaimResponse.model_validate(claims[link.claim_id])
                    for link in context.story_claims.get(story.id, [])
                    if link.claim_id in claims
                ],
            )
        )

    primary_story_ids = {
        link.story_id
        for link in context.links
        if link.story_role == MemoirChapterNodeStoryRole.PRIMARY.value
    }
    available_stories = list(
        (
            await db.scalars(
                select(MemoirStory)
                .where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.lifecycle_status
                    == MemoirStoryLifecycleStatus.ACTIVE.value,
                    MemoirStory.publication_status
                    == MemoirStoryPublicationStatus.ELIGIBLE.value,
                    MemoirStory.verification_status
                    != MemoirStoryVerificationStatus.REJECTED.value,
                    MemoirStory.id.not_in(primary_story_ids),
                )
                .order_by(MemoirStory.updated_at.desc(), MemoirStory.id)
            )
        ).all()
    )
    excluded_story_ids = (
        set(chapter_contracts.contract_constraints(context.contract).exclude_story_ids)
        if context.contract
        else set()
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
        available_stories=[
            MemoirChapterWorkspaceAvailableStoryResponse(
                id=story.id,
                title=story.title,
                story_type=story.story_type,
                summary=story.summary,
                completion_status=story.completion_status,
                verification_status=story.verification_status,
                story_version=story.version,
                excluded_from_chapter=story.id in excluded_story_ids,
            )
            for story in available_stories
        ],
        answer_updates=completion.answer_updates,
        gaps=completion.gap_groups,
        latest_refresh_job=(
            MemoirChapterCompletionRefreshJobResponse.model_validate(
                completion.refresh_job
            )
            if completion.refresh_job
            else None
        ),
    )
