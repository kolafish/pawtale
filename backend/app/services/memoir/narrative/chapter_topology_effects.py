from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import MemoirNarrativeGapStatus
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirChapterSourceAssignedBy,
    MemoirChapterSourceLinkStatus,
    MemoirEditingJobStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookImagePlacement,
    MemoirBookOutput,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirEditingJob,
)
from app.models.memoir_narrative import (
    MemoirClaimEvidence,
    MemoirNarrativeGap,
    MemoirNarrativeGapChapter,
    MemoirStoryClaim,
)
from app.services.memoir.narrative import gaps
from app.services.memoir.narrative.book_readiness import invalidate_formal_book_output


async def story_utterance_ids(
    db: AsyncSession,
    *,
    project_id: str,
    story_ids: set[str],
) -> set[str]:
    if not story_ids:
        return set()
    return {
        utterance_id
        for utterance_id in (
            await db.scalars(
                select(MemoirClaimEvidence.utterance_id)
                .join(
                    MemoirStoryClaim,
                    (MemoirStoryClaim.project_id == MemoirClaimEvidence.project_id)
                    & (MemoirStoryClaim.claim_id == MemoirClaimEvidence.claim_id),
                )
                .where(
                    MemoirClaimEvidence.project_id == project_id,
                    MemoirStoryClaim.story_id.in_(story_ids),
                    MemoirClaimEvidence.utterance_id.is_not(None),
                )
                .distinct()
            )
        ).all()
        if utterance_id
    }


async def copy_source_projection(
    db: AsyncSession,
    *,
    source_chapter: MemoirBookPlanChapter,
    target_chapter: MemoirBookPlanChapter,
    reason: str,
    utterance_ids: set[str] | None,
) -> None:
    query = select(MemoirChapterSourceLink).where(
        MemoirChapterSourceLink.project_id == source_chapter.project_id,
        MemoirChapterSourceLink.chapter_id == source_chapter.id,
        MemoirChapterSourceLink.status == MemoirChapterSourceLinkStatus.ACTIVE.value,
    )
    if utterance_ids is not None:
        if not utterance_ids:
            return
        query = query.where(MemoirChapterSourceLink.utterance_id.in_(utterance_ids))
    source_links = list((await db.scalars(query.order_by(MemoirChapterSourceLink.id))).all())
    if not source_links:
        return
    existing_links = list(
        (
            await db.scalars(
                select(MemoirChapterSourceLink).where(
                    MemoirChapterSourceLink.project_id == target_chapter.project_id,
                    MemoirChapterSourceLink.chapter_id == target_chapter.id,
                )
            )
        ).all()
    )
    existing_by_utterance = {link.utterance_id: link for link in existing_links}
    for link in source_links:
        existing = existing_by_utterance.get(link.utterance_id)
        if existing is not None:
            if existing.status == MemoirChapterSourceLinkStatus.REMOVED.value:
                existing.status = MemoirChapterSourceLinkStatus.ACTIVE.value
                existing.assigned_by = MemoirChapterSourceAssignedBy.USER.value
                existing.assignment_reason = reason
                existing.transcript_version_id = link.transcript_version_id
                existing.audio_file_id = link.audio_file_id
                existing.start_ms = link.start_ms
                existing.end_ms = link.end_ms
                existing.quote = link.quote
                existing.assignment_confidence = link.assignment_confidence
                existing.source_card_id = link.source_card_id
                existing.merged_draft_version_id = None
                existing.removed_at = None
                db.add(existing)
            continue
        db.add(
            MemoirChapterSourceLink(
                project_id=target_chapter.project_id,
                plan_id=target_chapter.plan_id,
                chapter_id=target_chapter.id,
                utterance_id=link.utterance_id,
                transcript_version_id=link.transcript_version_id,
                audio_file_id=link.audio_file_id,
                start_ms=link.start_ms,
                end_ms=link.end_ms,
                quote=link.quote,
                status=MemoirChapterSourceLinkStatus.ACTIVE.value,
                assigned_by=MemoirChapterSourceAssignedBy.USER.value,
                assignment_confidence=link.assignment_confidence,
                assignment_reason=reason,
                source_card_id=link.source_card_id,
            )
        )
    await db.flush()


async def invalidate_book_output(db: AsyncSession, *, project_id: str) -> None:
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    if output is not None:
        invalidate_formal_book_output(output)
        db.add(output)


async def resolve_invalidated_chapter_gaps(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    operation: str,
    idempotency_key: str,
) -> None:
    related_gap_ids = select(MemoirNarrativeGapChapter.gap_id).where(
        MemoirNarrativeGapChapter.project_id == project_id,
        MemoirNarrativeGapChapter.chapter_id == chapter_id,
    )
    gap_ids = list(
        (
            await db.scalars(
                select(MemoirNarrativeGap.id).where(
                    MemoirNarrativeGap.project_id == project_id,
                    MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
                    or_(
                        MemoirNarrativeGap.primary_chapter_id == chapter_id,
                        MemoirNarrativeGap.id.in_(related_gap_ids),
                    ),
                )
            )
        ).all()
    )
    note = f"章节{operation}后原问题的章节坐标已失效，请在当前结构上重新审计。"
    for gap_id in gap_ids:
        await gaps.resolve_gap_by_system(
            db,
            project_id=project_id,
            gap_id=gap_id,
            idempotency_key=f"chapter-{operation}:{idempotency_key}:resolve-gap:{gap_id}",
            note=note,
        )


async def move_chapter_image_placements(
    db: AsyncSession,
    *,
    source_chapter_id: str,
    target_chapter_id: str,
) -> None:
    placements = list(
        (
            await db.scalars(
                select(MemoirBookImagePlacement).where(
                    MemoirBookImagePlacement.chapter_id == source_chapter_id,
                )
            )
        ).all()
    )
    for placement in placements:
        placement.chapter_id = target_chapter_id
        db.add(placement)
    await db.flush()


def _retarget_source_review_metadata(
    metadata: dict[str, object],
    *,
    target: MemoirBookPlanChapter,
) -> dict[str, object]:
    updated = dict(metadata)
    route = updated.get("route")
    if isinstance(route, dict):
        updated["route"] = {
            **route,
            "target_chapter_id": target.id,
            "target_chapter_title": target.title,
        }
    else:
        updated["target_chapter_id"] = target.id
        updated["target_chapter_title"] = target.title
    return updated


async def retire_pending_chapter_work(
    db: AsyncSession,
    *,
    source: MemoirBookPlanChapter,
    target: MemoirBookPlanChapter,
) -> None:
    review_events = list(
        (
            await db.scalars(
                select(MemoirAutomationEvent).where(
                    MemoirAutomationEvent.project_id == source.project_id,
                    MemoirAutomationEvent.chapter_id == source.id,
                    MemoirAutomationEvent.event_type
                    == MemoirAutomationEventType.SOURCE_HELD.value,
                    MemoirAutomationEvent.status
                    == MemoirAutomationEventStatus.NEEDS_REVIEW.value,
                )
            )
        ).all()
    )
    for event in review_events:
        event.chapter_id = target.id
        event.summary_text = f"章节结构已调整，建议将这条讲述归入「{target.title}」。"
        event.metadata_json = _retarget_source_review_metadata(
            event.metadata_json or {},
            target=target,
        )
        db.add(event)

    pending_jobs = list(
        (
            await db.scalars(
                select(MemoirEditingJob).where(
                    MemoirEditingJob.project_id == source.project_id,
                    MemoirEditingJob.chapter_id == source.id,
                    MemoirEditingJob.status == MemoirEditingJobStatus.PENDING.value,
                )
            )
        ).all()
    )
    for job in pending_jobs:
        job.status = MemoirEditingJobStatus.SKIPPED.value
        job.result_json = {
            **(job.result_json or {}),
            "skip_reason": "chapter_merged",
            "merged_into_chapter_id": target.id,
        }
        job.error_message = "chapter was merged before the job started"
        db.add(job)
    await db.flush()
