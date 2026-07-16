from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.sql.elements import ColumnElement

from app.domain.memoir.narrative_types import (
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
)
from app.models.memoir_narrative import MemoirClaim


KNOWN_FOR_CONVERSATION_VERIFICATION_STATUSES = frozenset(
    {
        MemoirClaimVerificationStatus.UNREVIEWED.value,
        MemoirClaimVerificationStatus.CONFIRMED.value,
        MemoirClaimVerificationStatus.NEEDS_VERIFICATION.value,
        MemoirClaimVerificationStatus.DISPUTED.value,
    }
)
USABLE_FOR_PUBLICATION_VERIFICATION_STATUSES = frozenset(
    {
        MemoirClaimVerificationStatus.UNREVIEWED.value,
        MemoirClaimVerificationStatus.CONFIRMED.value,
        MemoirClaimVerificationStatus.NEEDS_VERIFICATION.value,
    }
)


@dataclass(frozen=True, slots=True)
class ClaimUsage:
    known_for_conversation: bool
    usable_for_publication: bool
    retain_as_conflict_context: bool
    successor_claim_id: str | None


def evaluate_claim_usage(claim: MemoirClaim) -> ClaimUsage:
    """Compute state-level use; evidence requirements remain with each consumer."""
    successor_claim_id = claim.superseded_by_claim_id
    current = successor_claim_id is None
    known_for_conversation = (
        current
        and claim.verification_status
        in KNOWN_FOR_CONVERSATION_VERIFICATION_STATUSES
    )
    usable_for_publication = (
        known_for_conversation
        and claim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value
        and claim.verification_status
        in USABLE_FOR_PUBLICATION_VERIFICATION_STATUSES
    )
    retain_as_conflict_context = (
        current
        and claim.verification_status
        in {
            MemoirClaimVerificationStatus.DISPUTED.value,
            MemoirClaimVerificationStatus.REJECTED.value,
        }
    )
    return ClaimUsage(
        known_for_conversation=known_for_conversation,
        usable_for_publication=usable_for_publication,
        retain_as_conflict_context=retain_as_conflict_context,
        successor_claim_id=successor_claim_id,
    )


def claim_known_for_conversation(claim: MemoirClaim) -> bool:
    return evaluate_claim_usage(claim).known_for_conversation


def claim_usable_for_publication(claim: MemoirClaim) -> bool:
    return evaluate_claim_usage(claim).usable_for_publication


def publication_safe_context_summary(
    claims: list[MemoirClaim],
    *,
    story_summary: str = "",
    limit: int = 2000,
) -> str:
    parts = [story_summary.strip()] if story_summary.strip() else []
    parts.extend(
        claim.statement.strip()
        for claim in claims
        if claim.statement.strip() and claim_usable_for_publication(claim)
    )
    return "；".join(dict.fromkeys(parts))[:limit]


def known_for_conversation_filters(
    model: type[MemoirClaim] = MemoirClaim,
) -> tuple[ColumnElement[bool], ...]:
    return (
        model.superseded_by_claim_id.is_(None),
        model.verification_status.in_(KNOWN_FOR_CONVERSATION_VERIFICATION_STATUSES),
    )


def usable_for_publication_filters(
    model: type[MemoirClaim] = MemoirClaim,
) -> tuple[ColumnElement[bool], ...]:
    return (
        *known_for_conversation_filters(model),
        model.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value,
        model.verification_status.in_(USABLE_FOR_PUBLICATION_VERIFICATION_STATUSES),
    )
