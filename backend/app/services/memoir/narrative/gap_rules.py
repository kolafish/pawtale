from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json

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


CHAPTER_COMPLETION_SIGNATURE_VERSION = "4"


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
    source_links: tuple[MemoirChapterSourceLink, ...]
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

    source_link_rows = list(
        (
            await db.scalars(
                select(MemoirChapterSourceLink)
                .where(
                    MemoirChapterSourceLink.project_id == project_id,
                    MemoirChapterSourceLink.chapter_id.in_(chapter_ids),
                )
                .order_by(
                    MemoirChapterSourceLink.chapter_id,
                    MemoirChapterSourceLink.created_at,
                    MemoirChapterSourceLink.id,
                )
            )
        ).all()
    )
    source_links_by_chapter: dict[str, list[MemoirChapterSourceLink]] = defaultdict(
        list
    )
    for link in source_link_rows:
        source_links_by_chapter[link.chapter_id].append(link)

    story_rows = (
        await db.execute(
            select(MemoirChapterSourceLink.chapter_id, MemoirStory)
            .join(
                MemoirClaimEvidence,
                (MemoirClaimEvidence.project_id == MemoirChapterSourceLink.project_id)
                & (
                    MemoirClaimEvidence.utterance_id
                    == MemoirChapterSourceLink.utterance_id
                )
                & (
                    MemoirChapterSourceLink.transcript_version_id.is_(None)
                    | (
                        MemoirClaimEvidence.transcript_version_id
                        == MemoirChapterSourceLink.transcript_version_id
                    )
                ),
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
                MemoirClaimEvidence.evidence_role
                == MemoirClaimEvidenceRole.SUPPORTS.value,
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

    chapter_evidence_rows = (
        await db.execute(
            select(
                MemoirChapterSourceLink.chapter_id,
                MemoirClaimEvidence.claim_id,
                func.count(MemoirClaimEvidence.id),
            )
            .join(
                MemoirClaimEvidence,
                (MemoirClaimEvidence.project_id == MemoirChapterSourceLink.project_id)
                & (
                    MemoirClaimEvidence.utterance_id
                    == MemoirChapterSourceLink.utterance_id
                )
                & (
                    MemoirChapterSourceLink.transcript_version_id.is_(None)
                    | (
                        MemoirClaimEvidence.transcript_version_id
                        == MemoirChapterSourceLink.transcript_version_id
                    )
                ),
            )
            .where(
                MemoirChapterSourceLink.project_id == project_id,
                MemoirChapterSourceLink.chapter_id.in_(chapter_ids),
                MemoirChapterSourceLink.status
                == MemoirChapterSourceLinkStatus.ACTIVE.value,
                MemoirClaimEvidence.evidence_role
                == MemoirClaimEvidenceRole.SUPPORTS.value,
            )
            .group_by(
                MemoirChapterSourceLink.chapter_id,
                MemoirClaimEvidence.claim_id,
            )
        )
    ).all()
    evidence_counts_by_chapter: dict[str, dict[str, int]] = defaultdict(dict)
    for chapter_id, claim_id, count in chapter_evidence_rows:
        evidence_counts_by_chapter[str(chapter_id)][str(claim_id)] = int(count)

    claim_ids = {
        claim_id
        for counts in evidence_counts_by_chapter.values()
        for claim_id in counts
    }
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
        chapter_evidence_counts = evidence_counts_by_chapter.get(chapter.id, {})
        chapter_claim_ids = set(chapter_evidence_counts) & {
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
            source_links=tuple(source_links_by_chapter.get(chapter.id, [])),
            stories=dict(chapter_stories),
            story_claims={
                story_id: [
                    link
                    for link in story_claims.get(story_id, [])
                    if link.claim_id in chapter_claim_ids
                ]
                for story_id in chapter_stories
            },
            claims={
                claim_id: claims[claim_id]
                for claim_id in chapter_claim_ids
                if claim_id in claims
            },
            evidence_counts={
                claim_id: chapter_evidence_counts.get(claim_id, 0)
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


def chapter_completion_content_signature(context: ChapterAuditContext) -> str:
    payload = {
        "engine_version": CHAPTER_COMPLETION_SIGNATURE_VERSION,
        "chapter_id": context.chapter.id,
        "chapter_title": context.chapter.title,
        "source_links": [
            [
                link.id,
                link.utterance_id,
                link.transcript_version_id,
                link.audio_file_id,
                link.start_ms,
                link.end_ms,
                sha256(link.quote.encode("utf-8")).hexdigest(),
                link.status,
                link.merged_draft_version_id,
                link.removed_at.isoformat() if link.removed_at else None,
                link.updated_at.isoformat(),
            ]
            for link in context.source_links
        ],
        "stories": [
            [story.id, story.version, story.updated_at.isoformat()]
            for story in sorted(context.stories.values(), key=lambda item: item.id)
        ],
        "story_claims": [
            [story_id, link.claim_id, link.narrative_role, link.is_core]
            for story_id, links in sorted(context.story_claims.items())
            for link in sorted(links, key=lambda item: (item.sort_order, item.id))
        ],
        "claims": [
            [
                claim.id,
                claim.updated_at.isoformat(),
                claim.verification_status,
                claim.publication_status,
                claim.superseded_by_claim_id,
                context.evidence_counts.get(claim.id, 0),
            ]
            for claim in sorted(context.claims.values(), key=lambda item: item.id)
        ],
        "draft": (
            [
                context.draft.id,
                context.draft.version_id,
                context.draft.version,
                context.draft.status,
                context.draft.source_changes_pending,
                sha256(context.draft.content.encode("utf-8")).hexdigest(),
            ]
            if context.draft
            else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


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
