from __future__ import annotations

from dataclasses import dataclass

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapActionDecisionSource,
    MemoirNarrativeGapActionReasonCode,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapType,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate


GAP_ACTION_POLICY_VERSION = "1"


@dataclass(frozen=True)
class GapActionPlan:
    recommended_action: MemoirNarrativeGapRecommendedAction
    reason_code: MemoirNarrativeGapActionReasonCode
    decision_source: MemoirNarrativeGapActionDecisionSource
    policy_version: str


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
    MemoirNarrativeGapActionReasonCode.MIGRATION_REVIEW_REQUIRED: (
        MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW
    ),
}


def _reason_for_candidate(
    candidate: MemoirNarrativeGapCandidate,
) -> MemoirNarrativeGapActionReasonCode:
    gap_type = candidate.gap_type
    target_slot = candidate.target_slot

    if target_slot == "chapter_contract":
        return MemoirNarrativeGapActionReasonCode.PUBLICATION_DECISION_REQUIRED
    if target_slot.startswith("node:"):
        if ":eligible-story:" in target_slot:
            return MemoirNarrativeGapActionReasonCode.PUBLICATION_DECISION_REQUIRED
        return MemoirNarrativeGapActionReasonCode.DRAFT_USAGE_PROBLEM
    if target_slot.startswith(("book:stale-block:", "book:block:")):
        return MemoirNarrativeGapActionReasonCode.DRAFT_USAGE_PROBLEM
    if target_slot.startswith("book:locked-block:"):
        return MemoirNarrativeGapActionReasonCode.FACT_CONFLICT_DETECTED

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
        return candidate.action_reason_signal

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
    return MemoirNarrativeGapActionReasonCode.MIGRATION_REVIEW_REQUIRED


def plan_gap_action(candidate: MemoirNarrativeGapCandidate) -> GapActionPlan:
    reason_code = _reason_for_candidate(candidate)
    return GapActionPlan(
        recommended_action=_ACTION_BY_REASON[reason_code],
        reason_code=reason_code,
        decision_source=MemoirNarrativeGapActionDecisionSource.DETERMINISTIC_POLICY,
        policy_version=GAP_ACTION_POLICY_VERSION,
    )
