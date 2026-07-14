from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirStoryLifecycleStatus,
    MemoirStoryRevisionChangeType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import MemoirStory, MemoirStoryClaim
from app.repositories.memoir_narrative import MemoirStoryRepository
from app.repositories.memoir_narrative.stories import normalize_story_title
from app.schemas.memoir_narrative import MemoirStoryCreate
from app.schemas.memoir_narrative.extraction import MemoirNarrativeExtractedStory
from app.services.memoir.narrative import stories as story_service


STORY_MATCH_CONFIDENCE_THRESHOLD = 0.9


def _validate_deterministic_story_identity(
    story: MemoirStory,
    candidate: MemoirNarrativeExtractedStory,
) -> None:
    if (
        story.story_type != candidate.story_type.value
        or story.title_normalized != normalize_story_title(candidate.title)
    ):
        raise ValueError("story extraction key changed identity for the same transcript version")


async def story_extraction_candidates(
    db: AsyncSession,
    *,
    project_id: str,
    limit: int = 50,
) -> list[dict[str, object]]:
    items = list(
        (
            await db.scalars(
                select(MemoirStory)
                .where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value,
                )
                .order_by(MemoirStory.updated_at.desc(), MemoirStory.id)
                .limit(max(1, min(limit, 100)))
            )
        ).all()
    )
    return [
        {
            "id": story.id,
            "title": story.title,
            "story_type": story.story_type,
            "summary": story.summary[:500],
            "time_start": story.time_start.isoformat() if story.time_start else None,
            "time_end": story.time_end.isoformat() if story.time_end else None,
            "time_precision": story.time_precision,
            "primary_place_entity_id": story.primary_place_entity_id,
        }
        for story in items
    ]


def _time_ranges_overlap(story: MemoirStory, candidate: MemoirNarrativeExtractedStory) -> bool:
    if not story.time_start or not story.time_end or not candidate.time_start or not candidate.time_end:
        return False
    return story.time_start <= candidate.time_end and candidate.time_start <= story.time_end


def _anchors_compatible(
    story: MemoirStory,
    candidate: MemoirNarrativeExtractedStory,
    *,
    primary_place_entity_id: str | None,
) -> bool:
    if (
        story.primary_place_entity_id
        and primary_place_entity_id
        and story.primary_place_entity_id != primary_place_entity_id
    ):
        return False
    if (
        story.time_start
        and story.time_end
        and candidate.time_start
        and candidate.time_end
        and not _time_ranges_overlap(story, candidate)
    ):
        return False
    return True


async def _story_claim_ids(db: AsyncSession, *, story: MemoirStory) -> set[str]:
    return set(
        (
            await db.scalars(
                select(MemoirStoryClaim.claim_id).where(
                    MemoirStoryClaim.project_id == story.project_id,
                    MemoirStoryClaim.story_id == story.id,
                )
            )
        ).all()
    )


async def _explicit_candidate_is_safe(
    db: AsyncSession,
    *,
    story: MemoirStory,
    candidate: MemoirNarrativeExtractedStory,
    primary_place_entity_id: str | None,
    claim_ids: set[str],
) -> bool:
    if story.story_type != candidate.story_type.value or not _anchors_compatible(
        story,
        candidate,
        primary_place_entity_id=primary_place_entity_id,
    ):
        return False
    same_title = story.title_normalized == normalize_story_title(candidate.title)
    same_place_and_time = bool(
        story.primary_place_entity_id
        and primary_place_entity_id == story.primary_place_entity_id
        and _time_ranges_overlap(story, candidate)
    )
    shared_claim = bool(claim_ids & await _story_claim_ids(db, story=story))
    return same_title or same_place_and_time or shared_claim


async def _has_structural_anchor(
    db: AsyncSession,
    *,
    story: MemoirStory,
    candidate: MemoirNarrativeExtractedStory,
    primary_place_entity_id: str | None,
    claim_ids: set[str],
) -> bool:
    same_place_and_time = bool(
        story.primary_place_entity_id
        and primary_place_entity_id == story.primary_place_entity_id
        and _time_ranges_overlap(story, candidate)
    )
    shared_claim = bool(claim_ids & await _story_claim_ids(db, story=story))
    return same_place_and_time or shared_claim


async def resolve_extracted_story(
    db: AsyncSession,
    *,
    project_id: str,
    transcript_version_id: str,
    candidate: MemoirNarrativeExtractedStory,
    primary_place_entity_id: str | None,
    claim_ids: set[str],
    force_needs_verification: bool = False,
    created_by: MemoirNarrativeActorType = MemoirNarrativeActorType.AI,
    creation_change_type: MemoirStoryRevisionChangeType = MemoirStoryRevisionChangeType.CREATED,
    creation_reason: str = "story candidate extracted from transcript evidence",
    allow_existing_story_reuse: bool = True,
) -> tuple[MemoirStory, tuple[str, ...], bool]:
    repository = MemoirStoryRepository(db)
    review_candidate_ids: list[str] = []
    if candidate.existing_story_id:
        suggested = await repository.get(
            project_id=project_id,
            story_id=candidate.existing_story_id,
        )
        if (
            suggested
            and suggested.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value
            and suggested.verification_status != MemoirStoryVerificationStatus.REJECTED.value
        ):
            if (
                allow_existing_story_reuse
                and candidate.match_confidence >= STORY_MATCH_CONFIDENCE_THRESHOLD
                and await _explicit_candidate_is_safe(
                    db,
                    story=suggested,
                    candidate=candidate,
                    primary_place_entity_id=primary_place_entity_id,
                    claim_ids=claim_ids,
                )
            ):
                return suggested, (), False
            review_candidate_ids.append(suggested.id)

    normalized_title = normalize_story_title(candidate.title)
    exact_matches = list(
        (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.story_type == candidate.story_type.value,
                    MemoirStory.title_normalized == normalized_title,
                    MemoirStory.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value,
                    MemoirStory.verification_status != MemoirStoryVerificationStatus.REJECTED.value,
                )
            )
        ).all()
    )
    compatible_matches: list[MemoirStory] = []
    for story in exact_matches:
        if not _anchors_compatible(
            story,
            candidate,
            primary_place_entity_id=primary_place_entity_id,
        ):
            continue
        if await _has_structural_anchor(
            db,
            story=story,
            candidate=candidate,
            primary_place_entity_id=primary_place_entity_id,
            claim_ids=claim_ids,
        ):
            compatible_matches.append(story)
    if allow_existing_story_reuse and len(compatible_matches) == 1:
        return compatible_matches[0], (), False
    review_candidate_ids.extend(story.id for story in exact_matches)
    review_candidate_ids = list(dict.fromkeys(review_candidate_ids))

    deterministic_id = str(
        uuid5(
            NAMESPACE_URL,
            f"pawtale:story:{project_id}:{transcript_version_id}:{candidate.key}",
        )
    )
    existing = await repository.get(project_id=project_id, story_id=deterministic_id)
    if existing:
        _validate_deterministic_story_identity(existing, candidate)
        if (
            existing.lifecycle_status == MemoirStoryLifecycleStatus.MERGED.value
            and existing.merged_into_story_id
        ):
            retained = await repository.get(
                project_id=project_id,
                story_id=existing.merged_into_story_id,
            )
            if retained is not None:
                return retained, (), False
        return existing, tuple(review_candidate_ids), False

    try:
        async with db.begin_nested():
            story = await story_service.create_story(
                db,
                data=MemoirStoryCreate(
                    project_id=project_id,
                    title=candidate.title,
                    story_type=candidate.story_type,
                    summary=candidate.summary,
                    time_start=candidate.time_start,
                    time_end=candidate.time_end,
                    time_precision=candidate.time_precision,
                    primary_place_entity_id=primary_place_entity_id,
                    verification_status=(
                        MemoirStoryVerificationStatus.NEEDS_VERIFICATION
                        if review_candidate_ids or force_needs_verification
                        else MemoirStoryVerificationStatus.UNREVIEWED
                    ),
                    created_by=created_by,
                ),
                story_id=deterministic_id,
                change_type=creation_change_type,
                reason=creation_reason,
            )
    except IntegrityError:
        concurrent = await repository.get(project_id=project_id, story_id=deterministic_id)
        if concurrent is None:
            raise
        _validate_deterministic_story_identity(concurrent, candidate)
        return concurrent, tuple(review_candidate_ids), False
    return story, tuple(review_candidate_ids), True
