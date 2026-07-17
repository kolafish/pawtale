from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import MemoirChapterSourceLinkStatus
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStoryClaim,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.book_plan_sources import source_change_counts
from app.services.memoir.book_plan_sources import DRAFT_STATUS_REASON_KEY
from app.services.memoir.narrative.claim_usage_policy import (
    claim_usable_for_publication,
)


@dataclass(frozen=True)
class ChapterAuditDraft:
    id: str
    version_id: str
    version: int
    status: str
    content: str
    updated_at_iso: str
    source_changes_pending: bool


@dataclass(frozen=True)
class ChapterAuditContext:
    chapter: MemoirBookPlanChapter
    stories: dict[str, MemoirStory]
    story_claims: dict[str, list[MemoirStoryClaim]]
    claims: dict[str, MemoirClaim]
    evidence_counts: dict[str, int]
    draft: ChapterAuditDraft | None = None


def _latest_draft_version(
    draft: MemoirBookChapterDraft,
) -> MemoirBookChapterDraftVersion | None:
    if not draft.versions:
        return None
    return max(
        draft.versions,
        key=lambda item: (item.version, item.created_at, item.id),
    )


async def load_chapter_audit_contexts(
    db: AsyncSession,
    *,
    project_id: str,
    chapters: list[MemoirBookPlanChapter],
) -> dict[str, ChapterAuditContext]:
    if any(chapter.project_id != project_id for chapter in chapters):
        raise ValueError("chapter does not belong to the project")
    chapter_ids = [chapter.id for chapter in chapters]
    if not chapter_ids:
        return {}

    story_rows = (
        await db.execute(
            select(MemoirChapterSourceLink.chapter_id, MemoirStory)
            .join(
                MemoirClaimEvidence,
                (MemoirClaimEvidence.project_id == MemoirChapterSourceLink.project_id)
                & (MemoirClaimEvidence.utterance_id == MemoirChapterSourceLink.utterance_id),
            )
            .join(
                MemoirStoryClaim,
                (MemoirStoryClaim.project_id == MemoirClaimEvidence.project_id)
                & (MemoirStoryClaim.claim_id == MemoirClaimEvidence.claim_id),
            )
            .join(
                MemoirStory,
                (MemoirStory.project_id == MemoirStoryClaim.project_id)
                & (MemoirStory.id == MemoirStoryClaim.story_id),
            )
            .where(
                MemoirChapterSourceLink.project_id == project_id,
                MemoirChapterSourceLink.chapter_id.in_(chapter_ids),
                MemoirChapterSourceLink.status
                == MemoirChapterSourceLinkStatus.ACTIVE.value,
                MemoirStory.lifecycle_status
                == MemoirStoryLifecycleStatus.ACTIVE.value,
            )
            .distinct()
        )
    ).all()
    stories_by_chapter: dict[str, dict[str, MemoirStory]] = defaultdict(dict)
    all_story_ids: set[str] = set()
    for chapter_id, story in story_rows:
        stories_by_chapter[str(chapter_id)][story.id] = story
        all_story_ids.add(story.id)

    story_claim_rows = (
        list(
            (
                await db.scalars(
                    select(MemoirStoryClaim)
                    .where(
                        MemoirStoryClaim.project_id == project_id,
                        MemoirStoryClaim.story_id.in_(all_story_ids),
                    )
                    .order_by(
                        MemoirStoryClaim.story_id,
                        MemoirStoryClaim.sort_order,
                        MemoirStoryClaim.id,
                    )
                )
            ).all()
        )
        if all_story_ids
        else []
    )
    story_claims: dict[str, list[MemoirStoryClaim]] = defaultdict(list)
    for link in story_claim_rows:
        story_claims[link.story_id].append(link)
    claim_ids = {link.claim_id for link in story_claim_rows}
    claims = (
        {
            claim.id: claim
            for claim in (
                await db.scalars(
                    select(MemoirClaim).where(
                        MemoirClaim.project_id == project_id,
                        MemoirClaim.id.in_(claim_ids),
                    )
                )
            ).all()
        }
        if claim_ids
        else {}
    )
    evidence_counts = (
        {
            str(claim_id): int(count)
            for claim_id, count in (
                await db.execute(
                    select(
                        MemoirClaimEvidence.claim_id,
                        func.count(MemoirClaimEvidence.id),
                    )
                    .where(
                        MemoirClaimEvidence.project_id == project_id,
                        MemoirClaimEvidence.claim_id.in_(claim_ids),
                        MemoirClaimEvidence.evidence_role
                        == MemoirClaimEvidenceRole.SUPPORTS.value,
                    )
                    .group_by(MemoirClaimEvidence.claim_id)
                )
            ).all()
        }
        if claim_ids
        else {}
    )
    drafts = {
        draft.chapter_id: draft
        for draft in (
            await db.scalars(
                select(MemoirBookChapterDraft)
                .where(
                    MemoirBookChapterDraft.project_id == project_id,
                    MemoirBookChapterDraft.chapter_id.in_(chapter_ids),
                )
                .options(selectinload(MemoirBookChapterDraft.versions))
            )
        ).all()
    }

    contexts: dict[str, ChapterAuditContext] = {}
    for chapter in chapters:
        chapter_stories = stories_by_chapter.get(chapter.id, {})
        chapter_claim_ids = {
            link.claim_id
            for story_id in chapter_stories
            for link in story_claims.get(story_id, [])
        }
        draft_model = drafts.get(chapter.id)
        latest = _latest_draft_version(draft_model) if draft_model else None
        pending_count, removed_count = source_change_counts(chapter.metadata_json)
        draft_metadata = (
            draft_model.metadata_json
            if draft_model is not None
            and isinstance(draft_model.metadata_json, dict)
            else {}
        )
        contexts[chapter.id] = ChapterAuditContext(
            chapter=chapter,
            stories=dict(chapter_stories),
            story_claims={
                story_id: list(story_claims.get(story_id, []))
                for story_id in chapter_stories
            },
            claims={
                claim_id: claims[claim_id]
                for claim_id in chapter_claim_ids
                if claim_id in claims
            },
            evidence_counts={
                claim_id: evidence_counts.get(claim_id, 0)
                for claim_id in chapter_claim_ids
                if claim_id in claims
            },
            draft=(
                ChapterAuditDraft(
                    id=draft_model.id,
                    version_id=latest.id,
                    version=latest.version,
                    status=draft_model.status,
                    content=latest.content,
                    updated_at_iso=draft_model.updated_at.isoformat(),
                    source_changes_pending=bool(
                        pending_count
                        or removed_count
                        or draft_metadata.get(DRAFT_STATUS_REASON_KEY)
                    ),
                )
                if draft_model is not None and latest is not None
                else None
            ),
        )
    return contexts


async def load_chapter_audit_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> ChapterAuditContext:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter is None:
        raise ValueError("chapter does not belong to the project")
    return (
        await load_chapter_audit_contexts(
            db,
            project_id=project_id,
            chapters=[chapter],
        )
    )[chapter.id]


def _chapter_candidate(
    context: ChapterAuditContext,
    *,
    gap_type: MemoirNarrativeGapType,
    severity: MemoirNarrativeGapSeverity,
    target_slot: str,
    title: str,
    description: str,
    story_ids: set[str] | None = None,
    claim_roles: dict[str, MemoirNarrativeGapClaimRole] | None = None,
) -> MemoirNarrativeGapCandidate:
    return MemoirNarrativeGapCandidate(
        scope_type=MemoirNarrativeGapScopeType.CHAPTER,
        primary_chapter_id=context.chapter.id,
        gap_type=gap_type,
        severity=severity,
        target_slot=target_slot,
        title=title,
        description=description,
        detected_by=MemoirNarrativeGapDetectedBy.RULES,
        claim_roles=claim_roles or {},
        involved_story_ids=story_ids or set(),
    )


def evaluate_deterministic_gaps(
    context: ChapterAuditContext,
) -> list[MemoirNarrativeGapCandidate]:
    usable_claim_ids = {
        claim.id
        for claim in context.claims.values()
        if claim_usable_for_publication(claim)
        and context.evidence_counts.get(claim.id, 0) > 0
    }
    if not usable_claim_ids:
        return [
            _chapter_candidate(
                context,
                gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot="chapter:material",
                title="本章还缺少可以写入正文的讲述",
                description="请先补充一段与本章主题直接相关的经历或感受。",
            )
        ]

    candidates: list[MemoirNarrativeGapCandidate] = []
    for story_id, story in context.stories.items():
        if (
            story.publication_status != MemoirStoryPublicationStatus.ELIGIBLE.value
            or story.verification_status == MemoirStoryVerificationStatus.REJECTED.value
        ):
            continue
        for story_claim in context.story_claims.get(story_id, []):
            claim = context.claims.get(story_claim.claim_id)
            if (
                claim is None
                or not claim_usable_for_publication(claim)
                or context.evidence_counts.get(claim.id, 0) > 0
            ):
                continue
            candidates.append(
                MemoirNarrativeGapCandidate(
                    scope_type=MemoirNarrativeGapScopeType.STORY,
                    primary_story_id=story_id,
                    gap_type=MemoirNarrativeGapType.UNSUPPORTED_FACT,
                    severity=(
                        MemoirNarrativeGapSeverity.HIGH
                        if story_claim.is_core
                        else MemoirNarrativeGapSeverity.MEDIUM
                    ),
                    target_slot=f"claim:{claim.id}:evidence",
                    title=f"“{story.title}”有一条事实缺少原始讲述",
                    description=claim.statement,
                    detected_by=MemoirNarrativeGapDetectedBy.RULES,
                    claim_roles={
                        claim.id: MemoirNarrativeGapClaimRole.UNSUPPORTED_CANDIDATE
                    },
                    involved_chapter_ids={context.chapter.id},
                )
            )
    return candidates
