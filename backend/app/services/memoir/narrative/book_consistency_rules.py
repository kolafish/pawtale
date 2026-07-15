from __future__ import annotations

from collections import defaultdict

from app.domain.memoir.narrative_types import (
    MemoirChapterNodeStoryRole,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRelationRole,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.narrative.book_consistency_context import BookConsistencyContext


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
        story_roles={
            story_id: MemoirNarrativeGapRelationRole.INVOLVED
            for story_id in sorted(story_ids or set())
        },
        chapter_roles={
            chapter_id: MemoirNarrativeGapRelationRole.INVOLVED
            for chapter_id in sorted(chapter_ids or set())
        },
    )


def _story_locations(
    context: BookConsistencyContext,
) -> tuple[dict[str, set[str]], list[tuple[tuple[int, int, int], str, str]]]:
    chapter_ids_by_story: dict[str, set[str]] = defaultdict(set)
    ordered_primary: list[tuple[tuple[int, int, int], str, str]] = []
    for chapter_index, chapter in enumerate(context.chapters):
        nodes = {node.id: node for node in chapter.nodes}
        for link in chapter.links:
            chapter_ids_by_story[link.story_id].add(chapter.chapter.id)
            if link.story_role != MemoirChapterNodeStoryRole.PRIMARY.value:
                continue
            node = nodes.get(link.node_id)
            if node is None:
                continue
            ordered_primary.append(
                (
                    (chapter_index, node.sort_order, link.sort_order),
                    link.story_id,
                    chapter.chapter.id,
                )
            )
    ordered_primary.sort(key=lambda item: item[0])
    return dict(chapter_ids_by_story), ordered_primary


def _eligible_stories(context: BookConsistencyContext):
    return {
        story.id: story
        for chapter in context.chapters
        for story in chapter.stories.values()
        if story.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value
        and story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
        and story.verification_status != MemoirStoryVerificationStatus.REJECTED.value
    }


def _claims_to_stories(context: BookConsistencyContext) -> dict[str, set[str]]:
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
    chapter_ids_by_story, ordered_primary = _story_locations(context)
    story_ids_by_claim = _claims_to_stories(context)

    # A later narrative position cannot end before an earlier exact time range starts.
    seen_ordered_stories: set[str] = set()
    unique_primary = []
    for position, story_id, chapter_id in ordered_primary:
        if story_id in seen_ordered_stories or story_id not in stories:
            continue
        seen_ordered_stories.add(story_id)
        unique_primary.append((position, stories[story_id], chapter_id))
    for earlier_index, (_, earlier, earlier_chapter_id) in enumerate(unique_primary):
        if (
            earlier.time_start is None
            or earlier.time_end is None
            or earlier.time_precision not in EXPLICIT_TIME_PRECISIONS
        ):
            continue
        for _, later, later_chapter_id in unique_primary[earlier_index + 1 :]:
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
                        f"“{later.title}”的结束时间早于排在前面的“{earlier.title}”开始时间，"
                        "需要调整章节顺序或确认时间。"
                    ),
                    story_ids={earlier.id, later.id},
                    chapter_ids={earlier_chapter_id, later_chapter_id},
                )
            )

    for story_id, chapter_ids in chapter_ids_by_story.items():
        primary_chapter_ids = {
            chapter_id
            for _, candidate_story_id, chapter_id in ordered_primary
            if candidate_story_id == story_id
        }
        if story_id not in stories or len(primary_chapter_ids) < 2:
            continue
        candidates.append(
            _book_candidate(
                gap_type=MemoirNarrativeGapType.REPETITION,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                target_slot=f"book:repetition:story:{story_id}",
                title=f"“{stories[story_id].title}”在多章作为主故事",
                description="同一 Story 在多个章节承担主要叙事，需确认是否重复或应拆分侧重点。",
                story_ids={story_id},
                chapter_ids=primary_chapter_ids,
            )
        )

    conflict_groups: dict[str, list] = defaultdict(list)
    for claim in context.claims.values():
        if (
            claim.conflict_group_id
            and not claim.superseded_by_claim_id
            and claim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value
            and claim.verification_status != MemoirClaimVerificationStatus.REJECTED.value
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
                description="这些 Claim 已被归入同一冲突组，正式成书前必须由用户确认或排除。",
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
            and context.entities[entity_id].entity_type == MemoirNarrativeEntityType.PERSON.value
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
                description="、".join(entity.canonical_name for entity in unresolved_people)
                + "的人物身份仍待确认。",
                claim_roles={claim.id: MemoirNarrativeGapClaimRole.SUPPORTING},
                story_ids=set(story_ids),
                chapter_ids={
                    chapter_id
                    for story_id in story_ids
                    for chapter_id in chapter_ids_by_story.get(story_id, set())
                },
            )
        )

    for item in context.blocks:
        block = item.block
        if (
            block.editorial_status == MemoirDraftBlockEditorialStatus.EXCLUDED.value
        ):
            continue
        if block.locked and item.locked_version_id and item.locked_version_id != item.version.id:
            candidates.append(
                _book_candidate(
                    gap_type=MemoirNarrativeGapType.CONTRADICTION,
                    severity=MemoirNarrativeGapSeverity.HIGH,
                    target_slot=f"book:locked-block:{block.id}",
                    title="锁定正文块在锁定后发生了变化",
                    description="当前正文版本与最近一次锁定时记录的版本不一致。",
                    story_ids=set(item.story_ids),
                    chapter_ids={block.chapter_id},
                )
            )
        if block.freshness_status == MemoirDraftBlockFreshnessStatus.STALE.value:
            candidates.append(
                _book_candidate(
                    gap_type=MemoirNarrativeGapType.UNSUPPORTED_FACT,
                    severity=MemoirNarrativeGapSeverity.HIGH,
                    target_slot=f"book:stale-block:{block.id}",
                    title="正文块引用的事实已经变化",
                    description="重新生成或人工核对该正文块后，才能作为正式书稿内容。",
                    story_ids=set(item.story_ids),
                    chapter_ids={block.chapter_id},
                )
            )
        if block.block_role != MemoirDraftBlockRole.FACTUAL.value:
            continue
        if not item.claim_ids:
            candidates.append(
                _book_candidate(
                    gap_type=MemoirNarrativeGapType.UNSUPPORTED_FACT,
                    severity=MemoirNarrativeGapSeverity.HIGH,
                    target_slot=f"book:block:{block.id}:claims",
                    title="事实正文块没有 Claim 来源",
                    description="事实性正文必须直接引用至少一条有效 Claim。",
                    story_ids=set(item.story_ids),
                    chapter_ids={block.chapter_id},
                )
            )
            continue
        for claim_id in item.claim_ids:
            claim = context.claims.get(claim_id)
            if claim is None:
                candidates.append(
                    _book_candidate(
                        gap_type=MemoirNarrativeGapType.UNSUPPORTED_FACT,
                        severity=MemoirNarrativeGapSeverity.HIGH,
                        target_slot=f"book:block:{block.id}:missing-claim:{claim_id}",
                        title="事实正文块引用了不存在的 Claim",
                        description="引用关系已损坏，需要重新生成该正文块。",
                        story_ids=set(item.story_ids),
                        chapter_ids={block.chapter_id},
                    )
                )
                continue
            invalid_reason = ""
            if claim.superseded_by_claim_id:
                invalid_reason = "Claim 已被新版本取代"
            elif claim.publication_status != MemoirClaimPublicationStatus.ELIGIBLE.value:
                invalid_reason = "Claim 不允许进入公开正文"
            elif claim.verification_status in {
                MemoirClaimVerificationStatus.DISPUTED.value,
                MemoirClaimVerificationStatus.REJECTED.value,
            }:
                invalid_reason = "Claim 尚有争议或已被否定"
            elif context.evidence_counts.get(claim.id, 0) < 1:
                invalid_reason = "Claim 没有有效支持证据"
            if invalid_reason:
                candidates.append(
                    _book_candidate(
                        gap_type=MemoirNarrativeGapType.UNSUPPORTED_FACT,
                        severity=MemoirNarrativeGapSeverity.HIGH,
                        target_slot=f"book:block:{block.id}:claim:{claim.id}",
                        title="事实正文块包含不可用于成书的 Claim",
                        description=invalid_reason + "。",
                        claim_roles={
                            claim.id: MemoirNarrativeGapClaimRole.UNSUPPORTED_CANDIDATE
                        },
                        story_ids=set(item.story_ids),
                        chapter_ids={block.chapter_id},
                    )
                )
    return candidates
