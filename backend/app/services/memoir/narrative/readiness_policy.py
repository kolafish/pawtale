from __future__ import annotations

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import MemoirStory


FORMAL_BLOCKING_GAP_TYPES = frozenset(
    {
        MemoirNarrativeGapType.CHRONOLOGY.value,
        MemoirNarrativeGapType.RELATIONSHIP.value,
        MemoirNarrativeGapType.CAUSALITY.value,
        MemoirNarrativeGapType.CONTRADICTION.value,
        MemoirNarrativeGapType.UNCLEAR_REFERENCE.value,
        MemoirNarrativeGapType.UNSUPPORTED_FACT.value,
    }
)


def story_can_support_draft(story: MemoirStory) -> bool:
    return (
        story.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value
        and story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
        and story.verification_status
        not in {
            MemoirStoryVerificationStatus.DISPUTED.value,
            MemoirStoryVerificationStatus.REJECTED.value,
        }
    )


def gap_blocks_formalization(*, gap_type: str, severity: str) -> bool:
    return (
        severity == MemoirNarrativeGapSeverity.HIGH.value
        and gap_type in FORMAL_BLOCKING_GAP_TYPES
    )
