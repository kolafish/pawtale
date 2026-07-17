from __future__ import annotations

from collections import defaultdict

from app.domain.memoir.narrative_types import (
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.narrative.book_consistency_context import (
    BookConsistencyContext,
)
from app.services.memoir.narrative.claim_usage_policy import (
    claim_known_for_conversation,
)


EXPLICIT_TIME_PRECISIONS = {
    MemoirClaimTimePrecision.EXACT.value,
    MemoirClaimTimePrecision.DAY.value,
    MemoirClaimTimePrecision.MONTH.value,
    MemoirClaimTimePrecision.YEAR.value,
}


def _book_candidate(
    *,
    gap_type: MemoirNarrativeGapType,
    severity: MemoirNarrativeGapSeverity,
    target_slot: str,
    title: str,
    description: str,
    claim_roles: dict[str, MemoirNarrativeGapClaimRole] | None = None,
    story_ids: set[str] | None = None,
    chapter_ids: set[str] | None = None,
) -> MemoirNarrativeGapCandidate:
    return MemoirNarrativeGapCandidate(
        scope_type=MemoirNarrativeGapScopeType.BOOK,
        gap_type=gap_type,
        severity=severity,
        target_slot=target_slot,
        title=title,
        description=description,
        detected_by=MemoirNarrativeGapDetectedBy.RULES,
        claim_roles=claim_roles or {},
        involved_story_ids=story_ids or set(),
        involved_chapter_ids=chapter_ids or set(),
    )


def _story_locations(
    context: BookConsistencyContext,
) -> tuple[dict[str, set[str]], list[tuple[tuple[int, int], str, str]]]:
    chapter_ids_by_story: dict[str, set[str]] = defaultdict(set)
    ordered: list[tuple[tuple[int, int], str, str]] = []
    for chapter_index, chapter in enumerate(context.chapters):
        stories = sorted(
            chapter.stories.values(),
            key=lambda story: (
                story.time_start is None,
                story.time_start,
                story.created_at,
                story.id,
            ),
        )
        for story_index, story in enumerate(stories):
            chapter_ids_by_story[story.id].add(chapter.chapter.id)
            ordered.append(
                ((chapter_index, story_index), story.id, chapter.chapter.id)
            )
    return dict(chapter_ids_by_story), ordered


def _eligible_stories(context: BookConsistencyContext):
    return {
        story.id: story
        for chapter in context.chapters
        for story in chapter.stories.values()
        if story.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value
        and story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
        and story.verification_status != MemoirStoryVerificationStatus.REJECTED.value
    }


def _claims_to_stories(
    context: BookConsistencyContext,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for chapter in context.chapters:
        for story_id, links in chapter.story_claims.items():
            for link in links:
                result[link.claim_id].add(story_id)
    return dict(result)


def evaluate_book_consistency_rules(
    context: BookConsistencyContext,
) -> list[MemoirNarrativeGapCandidate]:
    candidates: list[MemoirNarrativeGapCandidate] = []
    stories = _eligible_stories(context)
    chapter_ids_by_story, ordered_stories = _story_locations(context)
    story_ids_by_claim = _claims_to_stories(context)

    unique_ordered = []
    seen_story_ids: set[str] = set()
    for position, story_id, chapter_id in ordered_stories:
        if story_id in seen_story_ids or story_id not in stories:
            continue
        seen_story_ids.add(story_id)
        unique_ordered.append((position, stories[story_id], chapter_id))
    for index, (_, earlier, earlier_chapter_id) in enumerate(unique_ordered):
        if (
            earlier.time_start is None
            or earlier.time_end is None
            or earlier.time_precision not in EXPLICIT_TIME_PRECISIONS
        ):
            continue
        for _, later, later_chapter_id in unique_ordered[index + 1 :]:
            if (
                later.time_start is None
                or later.time_end is None
                or later.time_precision not in EXPLICIT_TIME_PRECISIONS
                or later.time_end >= earlier.time_start
            ):
                continue
            candidates.append(
                _book_candidate(
                    gap_type=MemoirNarrativeGapType.CHRONOLOGY,
                    severity=MemoirNarrativeGapSeverity.HIGH,
                    target_slot=f"book:chronology:{earlier.id}:{later.id}",
                    title="全书有明确的时间逆序",
                    description=(
                        f"“{later.title}”的结束时间早于排在前面的"
                        f"“{earlier.title}”开始时间，需要调整章节顺序或确认时间。"
                    ),
                    story_ids={earlier.id, later.id},
                    chapter_ids={earlier_chapter_id, later_chapter_id},
                )
            )

    for story_id, chapter_ids in chapter_ids_by_story.items():
        if story_id not in stories or len(chapter_ids) < 2:
            continue
        candidates.append(
            _book_candidate(
                gap_type=MemoirNarrativeGapType.REPETITION,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                target_slot=f"book:repetition:story:{story_id}",
                title=f"“{stories[story_id].title}”同时出现在多章",
                description="请确认各章是否有不同侧重点，避免正文重复。",
                story_ids={story_id},
                chapter_ids=chapter_ids,
            )
        )

    conflict_groups: dict[str, list] = defaultdict(list)
    for claim in context.claims.values():
        if (
            claim.conflict_group_id
            and claim_known_for_conversation(claim)
            and claim.publication_status
            == MemoirClaimPublicationStatus.ELIGIBLE.value
        ):
            conflict_groups[claim.conflict_group_id].append(claim)
    for conflict_group_id, claims in conflict_groups.items():
        if len(claims) < 2:
            continue
        claim_ids = {claim.id for claim in claims}
        story_ids = {
            story_id
            for claim_id in claim_ids
            for story_id in story_ids_by_claim.get(claim_id, set())
        }
        chapter_ids = {
            chapter_id
            for story_id in story_ids
            for chapter_id in chapter_ids_by_story.get(story_id, set())
        }
        candidates.append(
            _book_candidate(
                gap_type=MemoirNarrativeGapType.CONTRADICTION,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot=f"book:conflict-group:{conflict_group_id}",
                title="全书引用了尚未裁定的冲突事实",
                description="这些事实互相冲突，正式成书前需要用户确认或排除。",
                claim_roles={
                    claim_id: MemoirNarrativeGapClaimRole.CONFLICTING
                    for claim_id in sorted(claim_ids)
                },
                story_ids=story_ids,
                chapter_ids=chapter_ids,
            )
        )

    for claim in context.claims.values():
        if claim.claim_type != MemoirClaimType.RELATIONSHIP.value:
            continue
        entity_ids = {claim.subject_entity_id, claim.object_entity_id} - {None}
        unresolved_people = [
            context.entities[entity_id]
            for entity_id in entity_ids
            if entity_id in context.entities
            and context.entities[entity_id].entity_type
            == MemoirNarrativeEntityType.PERSON.value
            and context.entities[entity_id].verification_status
            not in {
                MemoirNarrativeEntityVerificationStatus.CONFIRMED.value,
                MemoirNarrativeEntityVerificationStatus.REJECTED.value,
            }
        ]
        if not unresolved_people:
            continue
        story_ids = story_ids_by_claim.get(claim.id, set())
        candidates.append(
            _book_candidate(
                gap_type=MemoirNarrativeGapType.RELATIONSHIP,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                target_slot=f"book:relationship:claim:{claim.id}",
                title="人物关系中的身份尚未确认",
                description=(
                    "、".join(
                        entity.canonical_name for entity in unresolved_people
                    )
                    + "的人物身份仍待确认。"
                ),
                claim_roles={
                    claim.id: MemoirNarrativeGapClaimRole.SUPPORTING
                },
                story_ids=set(story_ids),
                chapter_ids={
                    chapter_id
                    for story_id in story_ids
                    for chapter_id in chapter_ids_by_story.get(story_id, set())
                },
            )
        )
    return candidates
