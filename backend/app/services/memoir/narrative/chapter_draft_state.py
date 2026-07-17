from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChapterSourceLinkStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirChapterSourceLink,
)
from app.models.memoir_narrative import (
    MemoirClaimEvidence,
    MemoirStoryClaim,
)
from app.services.memoir.book_plan_sources import DRAFT_STATUS_REASON_KEY


FACTS_CHANGED_STATUS_REASON = "facts_changed"


async def mark_chapter_drafts_stale(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: set[str],
    reason: str = FACTS_CHANGED_STATUS_REASON,
) -> None:
    if not chapter_ids:
        return
    drafts = (
        await db.scalars(
            select(MemoirBookChapterDraft).where(
                MemoirBookChapterDraft.project_id == project_id,
                MemoirBookChapterDraft.chapter_id.in_(chapter_ids),
            )
        )
    ).all()
    for draft in drafts:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = {
            **(
                draft.metadata_json
                if isinstance(draft.metadata_json, dict)
                else {}
            ),
            DRAFT_STATUS_REASON_KEY: reason,
        }
        db.add(draft)


async def mark_chapter_drafts_stale_for_claim(
    db: AsyncSession,
    *,
    project_id: str,
    claim_id: str,
) -> None:
    chapter_ids = set(
        (
            await db.scalars(
                select(MemoirChapterSourceLink.chapter_id)
                .join(
                    MemoirClaimEvidence,
                    (MemoirClaimEvidence.project_id == MemoirChapterSourceLink.project_id)
                    & (
                        MemoirClaimEvidence.utterance_id
                        == MemoirChapterSourceLink.utterance_id
                    ),
                )
                .where(
                    MemoirChapterSourceLink.project_id == project_id,
                    MemoirChapterSourceLink.status
                    == MemoirChapterSourceLinkStatus.ACTIVE.value,
                    MemoirClaimEvidence.claim_id == claim_id,
                )
                .distinct()
            )
        ).all()
    )
    await mark_chapter_drafts_stale(
        db,
        project_id=project_id,
        chapter_ids=chapter_ids,
    )


async def mark_chapter_drafts_stale_for_story(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
) -> None:
    claim_ids = set(
        (
            await db.scalars(
                select(MemoirStoryClaim.claim_id).where(
                    MemoirStoryClaim.project_id == project_id,
                    MemoirStoryClaim.story_id == story_id,
                )
            )
        ).all()
    )
    for claim_id in claim_ids:
        await mark_chapter_drafts_stale_for_claim(
            db,
            project_id=project_id,
            claim_id=claim_id,
        )
