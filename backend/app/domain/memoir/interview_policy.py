from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Mapping

from app.domain.memoir.narrative_types import (
    MemoirConversationMomentum,
    MemoirInterviewAction,
    MemoirInterviewActionRisk,
    MemoirInterviewBriefSourceKind,
    MemoirInterviewStoryState,
)


INTERVIEW_BRIEF_SCHEMA_VERSION = "2"
SAFETY_CONSTRAINT_CODES = {
    "avoid_sensitive_topics",
    "do_not_continue",
    "pause_required",
    "privacy_restricted",
    "user_requested_pause",
}
USER_PREFERENCE_VALUES: Mapping[str, frozenset[str]] = {
    "detail_level": frozenset({"brief", "balanced", "detailed"}),
    "pace": frozenset({"slow", "balanced", "fast"}),
    "question_style": frozenset({"open", "balanced", "specific"}),
    "topic_switching": frozenset({"user_led", "balanced", "assistant_led"}),
}

ACTION_RISKS: Mapping[MemoirInterviewAction, MemoirInterviewActionRisk] = {
    MemoirInterviewAction.CONTINUE_LISTENING: MemoirInterviewActionRisk.LOW,
    MemoirInterviewAction.FOLLOW_CURRENT_STORY: MemoirInterviewActionRisk.LOW,
    MemoirInterviewAction.ASK_PERSON_RELATIONSHIP: MemoirInterviewActionRisk.LOW,
    MemoirInterviewAction.ASK_CAUSE_OR_RESULT: MemoirInterviewActionRisk.LOW,
    MemoirInterviewAction.SUMMARIZE_AND_CONFIRM: MemoirInterviewActionRisk.LOW,
    MemoirInterviewAction.RETURN_TO_PRIOR_STORY: MemoirInterviewActionRisk.MEDIUM,
    MemoirInterviewAction.OFFER_NEW_TOPIC: MemoirInterviewActionRisk.MEDIUM,
    MemoirInterviewAction.PAUSE_OR_END: MemoirInterviewActionRisk.TERMINAL,
}


@dataclass(frozen=True)
class InterviewActionCandidate:
    action: MemoirInterviewAction
    risk: MemoirInterviewActionRisk


@dataclass(frozen=True)
class InterviewBrief:
    source_kind: MemoirInterviewBriefSourceKind
    target_id: str | None
    purpose: str
    opening_question: str
    candidate_actions: tuple[InterviewActionCandidate, ...]
    current_story_state: MemoirInterviewStoryState
    conversation_momentum: MemoirConversationMomentum
    user_preferences: Mapping[str, object]
    safety_constraints: tuple[str, ...]
    existing_material: tuple[str, ...]
    unresolved_issue_ids: tuple[str, ...]
    candidate_topic_ids: tuple[str, ...]
    answer_hint: str
    completion_channel: str
    template_version: str | None
    policy_version: str

    def with_candidates(self) -> InterviewBrief:
        return replace(self, candidate_actions=build_candidate_actions(self))


def build_candidate_actions(
    brief: InterviewBrief,
) -> tuple[InterviewActionCandidate, ...]:
    safety_constraints = set(brief.safety_constraints)
    unknown_safety_constraints = safety_constraints - SAFETY_CONSTRAINT_CODES
    if (
        unknown_safety_constraints
        or "pause_required" in safety_constraints
        or "do_not_continue" in safety_constraints
    ):
        actions = (MemoirInterviewAction.PAUSE_OR_END,)
    elif (
        brief.current_story_state == MemoirInterviewStoryState.OPENING
        and brief.candidate_topic_ids
    ):
        actions = (MemoirInterviewAction.OFFER_NEW_TOPIC,)
    else:
        selected: list[MemoirInterviewAction] = [
            MemoirInterviewAction.CONTINUE_LISTENING,
        ]
        if brief.current_story_state in {
            MemoirInterviewStoryState.ACTIVE,
            MemoirInterviewStoryState.NEEDS_CONFIRMATION,
        }:
            selected.extend(
                (
                    MemoirInterviewAction.FOLLOW_CURRENT_STORY,
                    MemoirInterviewAction.ASK_PERSON_RELATIONSHIP,
                    MemoirInterviewAction.ASK_CAUSE_OR_RESULT,
                )
            )
        if brief.existing_material:
            selected.append(MemoirInterviewAction.SUMMARIZE_AND_CONFIRM)
        if (
            brief.source_kind == MemoirInterviewBriefSourceKind.OPEN_NARRATION
            and brief.existing_material
        ):
            selected.append(MemoirInterviewAction.RETURN_TO_PRIOR_STORY)
        if (
            brief.source_kind == MemoirInterviewBriefSourceKind.FRAMEWORK
            or brief.candidate_topic_ids
        ):
            selected.append(MemoirInterviewAction.OFFER_NEW_TOPIC)
        selected.append(MemoirInterviewAction.PAUSE_OR_END)
        actions = tuple(dict.fromkeys(selected))
    return tuple(
        InterviewActionCandidate(action=action, risk=ACTION_RISKS[action])
        for action in actions
    )


def _user_preference_snapshot(
    preferences: Mapping[str, object],
) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for key, allowed_values in USER_PREFERENCE_VALUES.items():
        value = preferences.get(key)
        if isinstance(value, str) and value in allowed_values:
            snapshot[key] = value
    return snapshot


def _safety_constraint_snapshot(codes: tuple[str, ...]) -> list[str]:
    known_codes = sorted(set(codes) & SAFETY_CONSTRAINT_CODES)
    if set(codes) - SAFETY_CONSTRAINT_CODES:
        known_codes.append("unknown_safety_constraint")
    return known_codes


def brief_context_snapshot(brief: InterviewBrief) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "schema_version": INTERVIEW_BRIEF_SCHEMA_VERSION,
        "source_kind": brief.source_kind.value,
        "framework_item_id": (
            brief.target_id
            if brief.source_kind == MemoirInterviewBriefSourceKind.FRAMEWORK
            else None
        ),
        "completion_issue_id": (
            brief.target_id
            if brief.source_kind == MemoirInterviewBriefSourceKind.BOOK_COMPLETION
            else None
        ),
        "template_version": brief.template_version,
        "policy_version": brief.policy_version,
        "completion_channel": brief.completion_channel,
        "current_story_state": brief.current_story_state.value,
        "conversation_momentum": brief.conversation_momentum.value,
        "user_preference_snapshot": _user_preference_snapshot(
            brief.user_preferences
        ),
        "safety_constraint_codes": _safety_constraint_snapshot(
            brief.safety_constraints
        ),
        "existing_material_refs": sorted(set(brief.existing_material)),
        "unresolved_issue_ids": sorted(set(brief.unresolved_issue_ids)),
        "answer_hint": brief.answer_hint,
        "candidate_framework_item_ids": sorted(
            set(brief.candidate_topic_ids)
        ),
    }
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot["brief_fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return snapshot
