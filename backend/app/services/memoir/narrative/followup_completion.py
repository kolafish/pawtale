from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSpeakerRole,
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirFollowupTaskClaimRole,
    MemoirFollowupTaskStatus,
    MemoirFollowupTaskUtteranceRole,
    MemoirNarrativeActorType,
    MemoirStorySlotStatus,
)
from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import (
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirFollowupTask,
    MemoirFollowupTaskClaim,
    MemoirFollowupTaskUtterance,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStorySlot,
    MemoirStorySlotClaim,
)
from app.services.memoir.narrative import followup_tasks, gaps


@dataclass(frozen=True)
class FollowupCompletionResult:
    task_id: str | None = None
    status: MemoirFollowupTaskStatus | None = None
    relation_role: MemoirFollowupTaskUtteranceRole | None = None
    answered_task_ids: tuple[str, ...] = ()


async def _new_effective_claim_ids(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    transcript_version_id: str,
) -> set[str]:
    return set(
        (
            await db.scalars(
                select(MemoirClaimEvidence.claim_id)
                .join(MemoirClaim, MemoirClaim.id == MemoirClaimEvidence.claim_id)
                .where(
                    MemoirClaimEvidence.project_id == project_id,
                    MemoirClaimEvidence.utterance_id == utterance_id,
                    MemoirClaimEvidence.transcript_version_id == transcript_version_id,
                    MemoirClaimEvidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value,
                    MemoirClaimEvidence.speaker_role
                    == MemoirClaimEvidenceSpeakerRole.NARRATOR.value,
                    MemoirClaim.project_id == project_id,
                    MemoirClaim.superseded_by_claim_id.is_(None),
                    MemoirClaim.verification_status != MemoirClaimVerificationStatus.REJECTED.value,
                    MemoirClaim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value,
                )
            )
        ).all()
    )


async def _target_slot_received_evidence(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
    target_slot: str,
    claim_ids: set[str],
) -> bool:
    if not claim_ids:
        return False
    story = await db.scalar(
        select(MemoirStory).where(
            MemoirStory.project_id == project_id,
            MemoirStory.id == story_id,
        )
    )
    if story is None:
        return False
    slot = await db.scalar(
        select(MemoirStorySlot).where(
            MemoirStorySlot.project_id == project_id,
            MemoirStorySlot.story_id == story_id,
            MemoirStorySlot.schema_version == story.slot_schema_version,
            MemoirStorySlot.slot_key == target_slot,
            MemoirStorySlot.status == MemoirStorySlotStatus.SUFFICIENT.value,
        )
    )
    if slot is None:
        return False
    supporting_claim = await db.scalar(
        select(MemoirStorySlotClaim.claim_id).where(
            MemoirStorySlotClaim.project_id == project_id,
            MemoirStorySlotClaim.story_slot_id == slot.id,
            MemoirStorySlotClaim.claim_id.in_(claim_ids),
            MemoirStorySlotClaim.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value,
        ).limit(1)
    )
    return supporting_claim is not None


async def _add_task_utterance_relation(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    utterance_id: str,
    role: MemoirFollowupTaskUtteranceRole,
) -> None:
    relation = await db.scalar(
        select(MemoirFollowupTaskUtterance).where(
            MemoirFollowupTaskUtterance.project_id == project_id,
            MemoirFollowupTaskUtterance.task_id == task_id,
            MemoirFollowupTaskUtterance.utterance_id == utterance_id,
        )
    )
    if relation is None:
        db.add(
            MemoirFollowupTaskUtterance(
                project_id=project_id,
                task_id=task_id,
                utterance_id=utterance_id,
                result_role=role.value,
            )
        )
    elif (
        role == MemoirFollowupTaskUtteranceRole.TARGET_ANSWER
        and relation.result_role != role.value
    ):
        relation.result_role = role.value
        db.add(relation)
    await db.flush()


async def evaluate_after_narrative_ingestion(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    transcript_version: MemoirTranscriptVersion,
) -> FollowupCompletionResult:
    if not utterance.chat_message_id:
        return FollowupCompletionResult()
    message_role = await db.scalar(
        select(MemoirChatMessage.role).where(
            MemoirChatMessage.project_id == project_id,
            MemoirChatMessage.id == utterance.chat_message_id,
        )
    )
    if message_role != MemoirChatMessageRole.USER.value:
        return FollowupCompletionResult()
    voice_session = await db.scalar(
        select(MemoirVoiceSession).where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceSession.id == utterance.voice_session_id,
        )
    )
    if voice_session is None or not voice_session.current_followup_task_id:
        current_task = None
    else:
        current_task = await followup_tasks.get_task(
            db,
            project_id=project_id,
            task_id=voice_session.current_followup_task_id,
        )
        if (
            current_task is None
            or current_task.status != MemoirFollowupTaskStatus.IN_PROGRESS.value
            or current_task.assigned_session_id != voice_session.id
        ):
            current_task = None

    new_claim_ids = await _new_effective_claim_ids(
        db,
        project_id=project_id,
        utterance_id=utterance.id,
        transcript_version_id=transcript_version.id,
    )
    if not new_claim_ids:
        return FollowupCompletionResult(
            task_id=current_task.id if current_task else None,
            status=(MemoirFollowupTaskStatus.IN_PROGRESS if current_task else None),
        )
    new_story_ids = set(
        (
            await db.scalars(
                select(MemoirStoryClaim.story_id).where(
                    MemoirStoryClaim.project_id == project_id,
                    MemoirStoryClaim.claim_id.in_(new_claim_ids),
                )
            )
        ).all()
    )
    candidates = list(
        (
            await db.scalars(
                select(MemoirFollowupTask)
                .options(*followup_tasks.task_load_options())
                .where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.status.in_(
                        [status.value for status in followup_tasks.ACTIVE_TASK_STATUSES]
                    ),
                )
                .order_by(MemoirFollowupTask.priority.desc(), MemoirFollowupTask.id)
            )
        ).unique().all()
    )
    answered_task_ids: list[str] = []
    current_relation_role: MemoirFollowupTaskUtteranceRole | None = None
    for task in candidates:
        task_claim_roles = {
            relation.claim_id: relation.context_role for relation in task.claims
        }
        related = bool(new_claim_ids & set(task_claim_roles)) or bool(
            task.story_id and task.story_id in new_story_ids
        )
        if not related:
            continue
        expected_evidence_received = any(
            claim_id in new_claim_ids
            and role == MemoirFollowupTaskClaimRole.EXPECTED_EVIDENCE.value
            for claim_id, role in task_claim_roles.items()
        )
        target_slot_completed = bool(
            task.story_id
            and task.target_slot
            and await _target_slot_received_evidence(
                db,
                project_id=project_id,
                story_id=task.story_id,
                target_slot=task.target_slot,
                claim_ids=new_claim_ids,
            )
        )
        answered = expected_evidence_received or target_slot_completed
        is_current_task = current_task is not None and task.id == current_task.id
        if not answered and not is_current_task:
            continue
        relation_role = (
            MemoirFollowupTaskUtteranceRole.TARGET_ANSWER
            if answered
            else MemoirFollowupTaskUtteranceRole.RELATED_CONTEXT
        )
        await _add_task_utterance_relation(
            db,
            project_id=project_id,
            task_id=task.id,
            utterance_id=utterance.id,
            role=relation_role,
        )
        if is_current_task:
            current_relation_role = relation_role
        if not answered:
            continue
        try:
            await followup_tasks.transition_task(
                db,
                project_id=project_id,
                task_id=task.id,
                status=MemoirFollowupTaskStatus.ANSWERED,
                actor_type=MemoirNarrativeActorType.SYSTEM,
                actor_user_id=None,
                idempotency_key=f"followup-answered:{task.id}:{transcript_version.id}",
                reason="new narrator evidence completed the follow-up target",
            )
        except followup_tasks.FollowupTaskConflictError:
            current = await followup_tasks.get_task(
                db,
                project_id=project_id,
                task_id=task.id,
            )
            if current is None or current.status != MemoirFollowupTaskStatus.ANSWERED.value:
                continue
        await gaps.resolve_gap_by_system(
            db,
            project_id=project_id,
            gap_id=task.gap_id,
            idempotency_key=f"gap-resolved-by-followup:{task.id}:{transcript_version.id}",
            note="新的讲述者事实证据已补足目标缺口。",
        )
        answered_task_ids.append(task.id)

    if current_task is not None:
        latest_current = await followup_tasks.get_task(
            db,
            project_id=project_id,
            task_id=current_task.id,
        )
        return FollowupCompletionResult(
            task_id=current_task.id,
            status=(
                MemoirFollowupTaskStatus(latest_current.status)
                if latest_current is not None
                else None
            ),
            relation_role=current_relation_role,
            answered_task_ids=tuple(answered_task_ids),
        )
    if answered_task_ids:
        return FollowupCompletionResult(
            task_id=answered_task_ids[0],
            status=MemoirFollowupTaskStatus.ANSWERED,
            relation_role=MemoirFollowupTaskUtteranceRole.TARGET_ANSWER,
            answered_task_ids=tuple(answered_task_ids),
        )
    return FollowupCompletionResult()
