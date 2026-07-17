from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
)
from app.models.memoir_narrative import MemoirNarrativeDecision
from app.repositories.memoir_narrative import MemoirNarrativeDecisionRepository
from app.schemas.memoir_narrative import MemoirNarrativeDecisionCreate


async def validate_source(
    db: AsyncSession,
    *,
    project_id: str,
    source_transcript_version_id: str | None,
) -> None:
    await MemoirNarrativeDecisionRepository(db).validate_transcript_source(
        project_id=project_id,
        source_transcript_version_id=source_transcript_version_id,
    )


async def existing_decision(
    db: AsyncSession,
    *,
    project_id: str,
    idempotency_key: str,
    target_type: MemoirNarrativeDecisionTargetType,
    target_id: str,
    action: MemoirNarrativeDecisionAction,
    actor_type: MemoirNarrativeActorType,
    actor_user_id: str | None,
    source_transcript_version_id: str | None = None,
) -> MemoirNarrativeDecision | None:
    decision = await MemoirNarrativeDecisionRepository(db).get_by_idempotency_key(
        project_id=project_id,
        idempotency_key=idempotency_key,
    )
    if decision is None:
        return None
    if (
        decision.target_type != target_type.value
        or decision.target_id != target_id
        or decision.action != action.value
        or decision.actor_type != actor_type.value
        or decision.actor_user_id != actor_user_id
        or decision.source_transcript_version_id != source_transcript_version_id
    ):
        raise ValueError("idempotency key was already used for another decision")
    return decision


async def record_decision(
    db: AsyncSession,
    *,
    project_id: str,
    target_type: MemoirNarrativeDecisionTargetType,
    target_id: str,
    action: MemoirNarrativeDecisionAction,
    actor_type: MemoirNarrativeActorType,
    actor_user_id: str | None,
    before_state: dict,
    after_state: dict,
    reason: str,
    idempotency_key: str,
    source_transcript_version_id: str | None = None,
) -> MemoirNarrativeDecision:
    decision, _created = await MemoirNarrativeDecisionRepository(db).create(
        MemoirNarrativeDecisionCreate(
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            source_transcript_version_id=source_transcript_version_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )
    return decision
