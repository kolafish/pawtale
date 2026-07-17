from __future__ import annotations

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapActionReasonCode,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapType,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate


_ACTION_BY_REASON = {
    MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE: (
        MemoirNarrativeGapRecommendedAction.INTERVIEW
    ),
    MemoirNarrativeGapActionReasonCode.EXISTING_MATERIAL_AVAILABLE: (
        MemoirNarrativeGapRecommendedAction.EDITORIAL
    ),
    MemoirNarrativeGapActionReasonCode.DRAFT_USAGE_PROBLEM: (
        MemoirNarrativeGapRecommendedAction.EDITORIAL
    ),
    MemoirNarrativeGapActionReasonCode.FACT_CONFLICT_DETECTED: (
        MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW
    ),
    MemoirNarrativeGapActionReasonCode.PUBLICATION_DECISION_REQUIRED: (
        MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW
    ),
}

_FACT_SENSITIVE_SEMANTIC_DIMENSIONS = {
    "person_introduction",
    "relationship_clarity",
    "reference",
}
_EDITORIAL_REASON_CODES = {
    MemoirNarrativeGapActionReasonCode.EXISTING_MATERIAL_AVAILABLE,
    MemoirNarrativeGapActionReasonCode.DRAFT_USAGE_PROBLEM,
}


def _semantic_dimension(candidate: MemoirNarrativeGapCandidate) -> str | None:
    if not candidate.target_slot.startswith("semantic:"):
        return None
    parts = candidate.target_slot.split(":", 2)
    return parts[1] if len(parts) >= 2 else None


def _reason_for_candidate(
    candidate: MemoirNarrativeGapCandidate,
) -> MemoirNarrativeGapActionReasonCode:
    gap_type = candidate.gap_type
    target_slot = candidate.target_slot

    if gap_type == MemoirNarrativeGapType.REPETITION:
        return MemoirNarrativeGapActionReasonCode.EXISTING_MATERIAL_AVAILABLE
    if gap_type == MemoirNarrativeGapType.CONTRADICTION:
        return MemoirNarrativeGapActionReasonCode.FACT_CONFLICT_DETECTED
    if gap_type == MemoirNarrativeGapType.UNSUPPORTED_FACT:
        return MemoirNarrativeGapActionReasonCode.PUBLICATION_DECISION_REQUIRED
    if gap_type == MemoirNarrativeGapType.CHRONOLOGY:
        if target_slot.startswith(("story-order:", "book:chronology:")):
            return MemoirNarrativeGapActionReasonCode.EXISTING_MATERIAL_AVAILABLE
        return MemoirNarrativeGapActionReasonCode.FACT_CONFLICT_DETECTED

    if candidate.action_reason_signal is not None:
        reason = candidate.action_reason_signal
        if (
            reason in _EDITORIAL_REASON_CODES
            and _semantic_dimension(candidate)
            in _FACT_SENSITIVE_SEMANTIC_DIMENSIONS
            and not candidate.claim_roles
        ):
            return MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE
        return reason

    if gap_type == MemoirNarrativeGapType.MISSING_SLOT:
        return MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE
    if candidate.scope_type == MemoirNarrativeGapScopeType.BOOK:
        return MemoirNarrativeGapActionReasonCode.PUBLICATION_DECISION_REQUIRED
    if gap_type in {
        MemoirNarrativeGapType.CAUSALITY,
        MemoirNarrativeGapType.RELATIONSHIP,
        MemoirNarrativeGapType.UNCLEAR_REFERENCE,
    }:
        return MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE
    return MemoirNarrativeGapActionReasonCode.PUBLICATION_DECISION_REQUIRED


def action_for_candidate(
    candidate: MemoirNarrativeGapCandidate,
) -> MemoirNarrativeGapRecommendedAction:
    return _ACTION_BY_REASON[_reason_for_candidate(candidate)]
