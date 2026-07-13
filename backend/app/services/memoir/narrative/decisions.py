from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
    MemoirNarrativeEntityVerificationStatus,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
    MemoirStory,
    MemoirStoryClaim,
)
from app.services.memoir.narrative import decision_log, stories as story_service


def _entity_state(entity: MemoirNarrativeEntity) -> dict[str, object]:
    return {
        "verification_status": entity.verification_status,
        "merged_into_entity_id": entity.merged_into_entity_id,
        "canonical_name": entity.canonical_name,
        "aliases": list(entity.aliases_json or []),
    }


def _claim_state(claim: MemoirClaim) -> dict[str, object]:
    return {
        "verification_status": claim.verification_status,
        "publication_status": claim.publication_status,
        "superseded_by_claim_id": claim.superseded_by_claim_id,
    }


async def decide_entity_verification(
    db: AsyncSession,
    *,
    project_id: str,
    entity_id: str,
    confirmed: bool,
    actor_user_id: str,
    idempotency_key: str,
    reason: str = "",
) -> MemoirNarrativeDecision:
    action = MemoirNarrativeDecisionAction.CONFIRM if confirmed else MemoirNarrativeDecisionAction.REJECT
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.ENTITY,
        target_id=entity_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        return existing
    entity = await db.scalar(
        select(MemoirNarrativeEntity).where(
            MemoirNarrativeEntity.project_id == project_id,
            MemoirNarrativeEntity.id == entity_id,
        )
    )
    if entity is None:
        raise ValueError("entity does not belong to the project")
    if entity.merged_into_entity_id:
        raise ValueError("merged entity cannot be confirmed or rejected")
    before = _entity_state(entity)
    entity.verification_status = (
        MemoirNarrativeEntityVerificationStatus.CONFIRMED.value
        if confirmed
        else MemoirNarrativeEntityVerificationStatus.REJECTED.value
    )
    entity.updated_by = MemoirNarrativeActorType.USER.value
    entity.updated_by_user_id = actor_user_id
    db.add(entity)
    return await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.ENTITY,
        target_id=entity_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=_entity_state(entity),
        reason=reason,
        idempotency_key=idempotency_key,
    )


async def merge_entities(
    db: AsyncSession,
    *,
    project_id: str,
    source_entity_id: str,
    retained_entity_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str = "",
) -> MemoirNarrativeDecision:
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.ENTITY,
        target_id=source_entity_id,
        action=MemoirNarrativeDecisionAction.MERGE,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        return existing
    if source_entity_id == retained_entity_id:
        raise ValueError("entity cannot be merged into itself")
    entities = list(
        (
            await db.scalars(
                select(MemoirNarrativeEntity).where(
                    MemoirNarrativeEntity.project_id == project_id,
                    MemoirNarrativeEntity.id.in_([source_entity_id, retained_entity_id]),
                )
            )
        ).all()
    )
    by_id = {entity.id: entity for entity in entities}
    source = by_id.get(source_entity_id)
    retained = by_id.get(retained_entity_id)
    if source is None or retained is None:
        raise ValueError("merge entities must belong to the same project")
    if source.entity_type != retained.entity_type:
        raise ValueError("only entities of the same type can be merged")
    if source.merged_into_entity_id or retained.merged_into_entity_id:
        raise ValueError("already merged entities cannot be merged again")

    before = {
        "source": _entity_state(source),
        "retained": _entity_state(retained),
    }
    retained.aliases_json = list(
        dict.fromkeys(
            [*(retained.aliases_json or []), source.canonical_name, *(source.aliases_json or [])]
        )
    )
    retained.updated_by = MemoirNarrativeActorType.USER.value
    retained.updated_by_user_id = actor_user_id
    source.merged_into_entity_id = retained.id
    source.updated_by = MemoirNarrativeActorType.USER.value
    source.updated_by_user_id = actor_user_id
    db.add_all([source, retained])
    return await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.ENTITY,
        target_id=source.id,
        action=MemoirNarrativeDecisionAction.MERGE,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state={
            "source": _entity_state(source),
            "retained": _entity_state(retained),
        },
        reason=reason,
        idempotency_key=idempotency_key,
    )


async def decide_claim_verification(
    db: AsyncSession,
    *,
    project_id: str,
    claim_id: str,
    confirmed: bool,
    actor_user_id: str,
    idempotency_key: str,
    reason: str = "",
) -> MemoirNarrativeDecision:
    action = MemoirNarrativeDecisionAction.CONFIRM if confirmed else MemoirNarrativeDecisionAction.REJECT
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.CLAIM,
        target_id=claim_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        return existing
    claim = await db.scalar(
        select(MemoirClaim).where(
            MemoirClaim.project_id == project_id,
            MemoirClaim.id == claim_id,
        )
    )
    if claim is None:
        raise ValueError("claim does not belong to the project")
    if claim.superseded_by_claim_id:
        raise ValueError("superseded claim cannot be confirmed or rejected")
    if confirmed:
        evidence_count = await db.scalar(
            select(func.count(MemoirClaimEvidence.id)).where(
                MemoirClaimEvidence.project_id == project_id,
                MemoirClaimEvidence.claim_id == claim_id,
                MemoirClaimEvidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value,
            )
        )
        if not evidence_count:
            raise ValueError("claim requires supporting evidence before confirmation")
    before = _claim_state(claim)
    claim.verification_status = (
        MemoirClaimVerificationStatus.CONFIRMED.value
        if confirmed
        else MemoirClaimVerificationStatus.REJECTED.value
    )
    claim.updated_by = MemoirNarrativeActorType.USER.value
    claim.updated_by_user_id = actor_user_id
    db.add(claim)
    decision = await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.CLAIM,
        target_id=claim.id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=_claim_state(claim),
        reason=reason,
        idempotency_key=idempotency_key,
    )
    await _recompute_stories_for_claim(
        db,
        claim=claim,
        reason=f"claim verification changed by user decision {decision.id}",
    )
    return decision

async def decide_claim_publication(
    db: AsyncSession,
    *,
    project_id: str,
    claim_id: str,
    publication_status: MemoirClaimPublicationStatus,
    actor_user_id: str,
    idempotency_key: str,
    reason: str = "",
) -> MemoirNarrativeDecision:
    if publication_status == MemoirClaimPublicationStatus.ELIGIBLE:
        raise ValueError("eligible restoration requires a separate review decision")
    action = (
        MemoirNarrativeDecisionAction.MARK_PRIVATE
        if publication_status == MemoirClaimPublicationStatus.PRIVATE
        else MemoirNarrativeDecisionAction.EXCLUDE
    )
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.CLAIM,
        target_id=claim_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        return existing
    claim = await db.scalar(
        select(MemoirClaim).where(
            MemoirClaim.project_id == project_id,
            MemoirClaim.id == claim_id,
        )
    )
    if claim is None:
        raise ValueError("claim does not belong to the project")
    before = _claim_state(claim)
    claim.publication_status = publication_status.value
    claim.updated_by = MemoirNarrativeActorType.USER.value
    claim.updated_by_user_id = actor_user_id
    db.add(claim)
    decision = await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.CLAIM,
        target_id=claim.id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=_claim_state(claim),
        reason=reason,
        idempotency_key=idempotency_key,
    )
    await _recompute_stories_for_claim(
        db,
        claim=claim,
        reason=f"claim publication changed by user decision {decision.id}",
    )
    return decision


async def mark_claim_needs_review(
    db: AsyncSession,
    *,
    claim: MemoirClaim,
    reason: str,
    idempotency_key: str,
) -> MemoirNarrativeDecision:
    existing = await decision_log.existing_decision(
        db,
        project_id=claim.project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.CLAIM,
        target_id=claim.id,
        action=MemoirNarrativeDecisionAction.NEEDS_REVIEW,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
    )
    if existing is not None:
        return existing
    before = _claim_state(claim)
    claim.verification_status = MemoirClaimVerificationStatus.NEEDS_VERIFICATION.value
    claim.updated_by = MemoirNarrativeActorType.SYSTEM.value
    claim.updated_by_user_id = None
    db.add(claim)
    return await decision_log.record_decision(
        db,
        project_id=claim.project_id,
        target_type=MemoirNarrativeDecisionTargetType.CLAIM,
        target_id=claim.id,
        action=MemoirNarrativeDecisionAction.NEEDS_REVIEW,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
        before_state=before,
        after_state=_claim_state(claim),
        reason=reason,
        idempotency_key=idempotency_key,
    )


async def _recompute_stories_for_claim(
    db: AsyncSession,
    *,
    claim: MemoirClaim,
    reason: str,
) -> None:
    story_ids = set(
        (
            await db.scalars(
                select(MemoirStoryClaim.story_id).where(
                    MemoirStoryClaim.project_id == claim.project_id,
                    MemoirStoryClaim.claim_id == claim.id,
                )
            )
        ).all()
    )
    if not story_ids:
        return
    linked_stories = list(
        (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == claim.project_id,
                    MemoirStory.id.in_(story_ids),
                )
            )
        ).all()
    )
    for story in linked_stories:
        await story_service.recompute_story_with_revision(db, story=story, reason=reason)
