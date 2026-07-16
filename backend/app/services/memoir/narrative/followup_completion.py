from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MEMOIR_STORY_NARRATION_TARGET,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSpeakerRole,
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirFollowupTaskClaimRole,
    MemoirFollowupTaskStatus,
    MemoirFollowupTaskAnswerRole,
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerKind,
    MemoirFollowupAnswerUse,
    MemoirNarrativeActorType,
)
from app.domain.memoir.types import MemoirChatMessageRole, MemoirSessionGoal
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
    MemoirFollowupTaskAnswer,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStorySlot,
    MemoirStorySlotClaim,
    MemoirUtteranceInterviewAssignment,
)
from app.schemas.memoir_book import MemoirFollowupAnswerInterpretationResponse
from app.services.memoir import automation_event_resolution
from app.services.memoir.followup_answer_parts import validate_resolved_answer_parts
from app.services.memoir.narrative import (
    draft_blocks,
    fact_resolution,
    followup_tasks,
    completion_refresh,
    gaps,
    interview_policy,
)


@dataclass(frozen=True)
class FollowupCompletionResult:
    task_id: str | None = None
    status: MemoirFollowupTaskStatus | None = None
    relation_role: MemoirFollowupTaskAnswerRole | None = None
    answered_task_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FollowupIngestionContext:
    task_id: str
    chapter_id: str | None
    question: str
    purpose: str
    expected_answer_use: str
    known_context_summary: str
    target_slot: str
    story_id: str | None
    story_title: str
    story_type: str
    story_summary: str
    interpretation_status: str
    answer_kind: str | None
    direct_narrative_text: str
    clarification_text: str
    fact_resolution_text: str


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


async def _add_task_answer_relation(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    utterance_id: str,
    role: MemoirFollowupTaskAnswerRole,
) -> MemoirFollowupTaskAnswer:
    relation = await db.scalar(
        select(MemoirFollowupTaskAnswer).where(
            MemoirFollowupTaskAnswer.project_id == project_id,
            MemoirFollowupTaskAnswer.task_id == task_id,
            MemoirFollowupTaskAnswer.utterance_id == utterance_id,
        )
    )
    if relation is None:
        relation = MemoirFollowupTaskAnswer(
            project_id=project_id,
            task_id=task_id,
            utterance_id=utterance_id,
            result_role=role.value,
        )
        db.add(relation)
    else:
        if (
            role == MemoirFollowupTaskAnswerRole.TARGET_ANSWER
            and relation.result_role != role.value
        ):
            relation.result_role = role.value
            db.add(relation)
    await db.flush()
    return relation


async def link_utterance_to_session_task(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    utterance: MemoirVoiceUtterance,
) -> str | None:
    """Persist the creation-time interview target before async processing starts."""
    task: MemoirFollowupTask | None = None
    if (
        voice_session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value
        and voice_session.current_followup_task_id
    ):
        candidate = await followup_tasks.get_task(
            db,
            project_id=voice_session.project_id,
            task_id=voice_session.current_followup_task_id,
        )
        if (
            candidate is not None
            and candidate.status == MemoirFollowupTaskStatus.IN_PROGRESS.value
            and candidate.assigned_session_id == voice_session.id
        ):
            task = candidate
    await interview_policy.assign_utterance(
        db,
        utterance=utterance,
        followup_task=task,
    )
    if task is None:
        return None
    await _add_task_answer_relation(
        db,
        project_id=voice_session.project_id,
        task_id=task.id,
        utterance_id=utterance.id,
        role=MemoirFollowupTaskAnswerRole.RELATED_CONTEXT,
    )
    return task.id


async def ingestion_context_for_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
) -> FollowupIngestionContext | None:
    assignment = await db.scalar(
        select(MemoirUtteranceInterviewAssignment).where(
            MemoirUtteranceInterviewAssignment.utterance_id == utterance.id,
        )
    )
    if assignment is None or not assignment.followup_task_id:
        return None
    task = await followup_tasks.get_task(
        db,
        project_id=project_id,
        task_id=assignment.followup_task_id,
    )
    if task is None:
        return None
    context_statuses = {
        *followup_tasks.ACTIVE_TASK_STATUSES,
        MemoirFollowupTaskStatus.ANSWERED,
    }
    if MemoirFollowupTaskStatus(task.status) not in context_statuses:
        return None
    assigned_answer = await db.scalar(
        select(MemoirFollowupTaskAnswer).where(
            MemoirFollowupTaskAnswer.project_id == project_id,
            MemoirFollowupTaskAnswer.task_id == assignment.followup_task_id,
            MemoirFollowupTaskAnswer.utterance_id == utterance.id,
        )
    )
    story = None
    if task.story_id:
        story = await db.scalar(
            select(MemoirStory).where(
                MemoirStory.project_id == project_id,
                MemoirStory.id == task.story_id,
            )
        )
    return FollowupIngestionContext(
        task_id=task.id,
        chapter_id=task.chapter_id,
        question=task.question,
        purpose=task.purpose,
        expected_answer_use=task.answer_use,
        known_context_summary=task.known_context_summary,
        target_slot=task.target_slot,
        story_id=story.id if story else None,
        story_title=story.title if story else "",
        story_type=story.story_type if story else "",
        story_summary=story.summary if story else "",
        interpretation_status=(
            assigned_answer.interpretation_status
            if assigned_answer
            else MemoirFollowupAnswerInterpretationStatus.PENDING.value
        ),
        answer_kind=assigned_answer.answer_kind if assigned_answer else None,
        direct_narrative_text=(
            assigned_answer.direct_narrative_text if assigned_answer else ""
        ),
        clarification_text=(
            assigned_answer.clarification_text if assigned_answer else ""
        ),
        fact_resolution_text=(
            assigned_answer.fact_resolution_text if assigned_answer else ""
        ),
    )


async def record_answer_interpretation(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    interpretation: MemoirFollowupAnswerInterpretationResponse,
) -> MemoirFollowupTaskAnswer:
    relation = await db.scalar(
        select(MemoirFollowupTaskAnswer)
        .join(
            MemoirUtteranceInterviewAssignment,
            MemoirUtteranceInterviewAssignment.utterance_id
            == MemoirFollowupTaskAnswer.utterance_id,
        )
        .where(
            MemoirFollowupTaskAnswer.project_id == project_id,
            MemoirFollowupTaskAnswer.task_id == interpretation.task_id,
            MemoirFollowupTaskAnswer.utterance_id == utterance_id,
            MemoirUtteranceInterviewAssignment.followup_task_id
            == MemoirFollowupTaskAnswer.task_id,
        )
    )
    if relation is None:
        raise ValueError("补全回答缺少采集任务快照")
    relation.interpretation_status = interpretation.status.value
    relation.answer_kind = (
        interpretation.answer_kind.value if interpretation.answer_kind else None
    )
    relation.direct_narrative_text = interpretation.direct_narrative_text
    relation.clarification_text = interpretation.clarification_text
    relation.fact_resolution_text = interpretation.fact_resolution_text
    relation.confirmation_question = interpretation.confirmation_question
    relation.interpretation_reason = interpretation.reason
    db.add(relation)
    await db.flush()
    return relation


async def confirm_answer_interpretation(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    utterance_id: str,
    answer_kind: MemoirFollowupAnswerKind,
    direct_narrative_text: str,
    clarification_text: str,
    fact_resolution_text: str,
    ai_model: str | None = None,
) -> MemoirFollowupTaskAnswer:
    relation = await db.scalar(
        select(MemoirFollowupTaskAnswer)
        .join(
            MemoirUtteranceInterviewAssignment,
            MemoirUtteranceInterviewAssignment.utterance_id
            == MemoirFollowupTaskAnswer.utterance_id,
        )
        .where(
            MemoirFollowupTaskAnswer.project_id == project_id,
            MemoirFollowupTaskAnswer.task_id == task_id,
            MemoirFollowupTaskAnswer.utterance_id == utterance_id,
            MemoirUtteranceInterviewAssignment.followup_task_id
            == MemoirFollowupTaskAnswer.task_id,
        )
    )
    if relation is None:
        raise ValueError("补全回答缺少采集任务快照")
    if (
        relation.interpretation_status
        != MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION.value
    ):
        raise ValueError("补全回答当前不需要确认")
    utterance = await db.get(MemoirVoiceUtterance, utterance_id)
    if utterance is None:
        raise ValueError("补全回答对应的讲述记录不存在")
    source_text = (utterance.transcript_edited or "").strip() or (
        utterance.transcript_raw or ""
    ).strip()
    validate_resolved_answer_parts(
        source_text=source_text,
        answer_kind=answer_kind,
        direct_narrative_text=direct_narrative_text,
        clarification_text=clarification_text,
        fact_resolution_text=fact_resolution_text,
    )
    relation.interpretation_status = MemoirFollowupAnswerInterpretationStatus.RESOLVED.value
    relation.answer_kind = answer_kind.value
    relation.direct_narrative_text = direct_narrative_text
    relation.clarification_text = clarification_text
    relation.fact_resolution_text = fact_resolution_text
    relation.confirmation_question = ""
    relation.interpretation_reason = "用户确认了该回答在章节中的用途。"
    db.add(relation)
    await db.flush()
    await complete_task_from_semantic_interpretation(
        db,
        project_id=project_id,
        relation=relation,
        ai_model=ai_model,
    )
    return relation


async def complete_task_from_semantic_interpretation(
    db: AsyncSession,
    *,
    project_id: str,
    relation: MemoirFollowupTaskAnswer,
    ai_model: str | None = None,
) -> bool:
    if relation.interpretation_status != MemoirFollowupAnswerInterpretationStatus.RESOLVED.value:
        return False
    task = await followup_tasks.get_task(
        db,
        project_id=project_id,
        task_id=relation.task_id,
    )
    if task is None:
        raise ValueError("补全回答对应的任务不存在")
    expected_use = MemoirFollowupAnswerUse(task.answer_use)
    answered = (
        expected_use == MemoirFollowupAnswerUse.CLARIFICATION
        and bool(relation.clarification_text)
    ) or (
        expected_use == MemoirFollowupAnswerUse.DIRECT_NARRATIVE
        and task.target_slot.startswith("semantic:")
        and bool(relation.clarification_text)
    ) or (
        expected_use == MemoirFollowupAnswerUse.FACT_RESOLUTION
        and bool(relation.fact_resolution_text)
    )
    if not answered:
        return False
    if expected_use == MemoirFollowupAnswerUse.FACT_RESOLUTION:
        if not await fact_resolution.resolve_fact_answer(
            db,
            project_id=project_id,
            task=task,
            relation=relation,
            ai_model=ai_model,
        ):
            return False
    relation.result_role = MemoirFollowupTaskAnswerRole.TARGET_ANSWER.value
    db.add(relation)
    if task.status != MemoirFollowupTaskStatus.ANSWERED.value:
        await followup_tasks.transition_task(
            db,
            project_id=project_id,
            task_id=task.id,
            status=MemoirFollowupTaskStatus.ANSWERED,
            actor_type=MemoirNarrativeActorType.SYSTEM,
            actor_user_id=None,
            idempotency_key=f"followup-interpreted:{task.id}:{relation.utterance_id}",
            reason="resolved follow-up answer supplied the requested semantic clarification",
        )
    await gaps.resolve_gap_by_system(
        db,
        project_id=project_id,
        gap_id=task.gap_id,
        idempotency_key=f"gap-resolved-by-interpretation:{task.id}:{relation.utterance_id}",
        note="用户回答已明确补充所需的解释或事实判断。",
    )
    await automation_event_resolution.resolve_followup_source_review_events(
        db,
        project_id=project_id,
        task_id=task.id,
        resolved_by_utterance_id=relation.utterance_id,
    )
    if task.chapter_id:
        if relation.clarification_text.strip():
            await draft_blocks.mark_blocks_stale_for_editorial_context(
                db,
                project_id=project_id,
                chapter_id=task.chapter_id,
                story_id=task.story_id,
            )
        await completion_refresh.enqueue_chapter_completion_refresh(
            db,
            project_id=project_id,
            chapter_id=task.chapter_id,
            include_semantic_audit=task.target_slot.startswith("semantic:"),
            ai_model=None,
            source="book_completion_semantic_answer",
        )
    await db.flush()
    return True


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
    ingestion_context = await ingestion_context_for_utterance(
        db,
        project_id=project_id,
        utterance=utterance,
    )
    current_task = (
        await followup_tasks.get_task(
            db,
            project_id=project_id,
            task_id=ingestion_context.task_id,
        )
        if ingestion_context
        else None
    )

    new_claim_ids = await _new_effective_claim_ids(
        db,
        project_id=project_id,
        utterance_id=utterance.id,
        transcript_version_id=transcript_version.id,
    )
    if not new_claim_ids:
        return FollowupCompletionResult(
            task_id=current_task.id if current_task else None,
            status=(MemoirFollowupTaskStatus(current_task.status) if current_task else None),
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
    chapters_to_reaudit: set[str] = set()
    chapters_to_semantic_reaudit: set[str] = set()
    current_relation_role: MemoirFollowupTaskAnswerRole | None = None
    for task in candidates:
        task_claim_roles = {
            relation.claim_id: relation.context_role for relation in task.claims
        }
        is_current_task = current_task is not None and task.id == current_task.id
        related = bool(new_claim_ids & set(task_claim_roles)) or bool(
            task.story_id and task.story_id in new_story_ids
        )
        if not related and not is_current_task:
            continue
        expected_evidence_received = any(
            claim_id in new_claim_ids
            and role == MemoirFollowupTaskClaimRole.EXPECTED_EVIDENCE.value
            for claim_id, role in task_claim_roles.items()
        )
        target_slot_completed = bool(
            task.story_id
            and task.target_slot
            and task.target_slot != MEMOIR_STORY_NARRATION_TARGET
            and await _target_slot_received_evidence(
                db,
                project_id=project_id,
                story_id=task.story_id,
                target_slot=task.target_slot,
                claim_ids=new_claim_ids,
            )
        )
        broad_story_answered = bool(
            task.story_id
            and task.target_slot == MEMOIR_STORY_NARRATION_TARGET
            and (is_current_task or task.story_id in new_story_ids)
        )
        semantic_narrative_answered = bool(
            is_current_task
            and task.target_slot.startswith("semantic:")
            and new_claim_ids
        )
        answered = (
            expected_evidence_received
            or target_slot_completed
            or broad_story_answered
            or semantic_narrative_answered
        )
        if not answered and not is_current_task:
            continue
        relation_role = (
            MemoirFollowupTaskAnswerRole.TARGET_ANSWER
            if answered
            else MemoirFollowupTaskAnswerRole.RELATED_CONTEXT
        )
        await _add_task_answer_relation(
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
        if (
            is_current_task
            and ingestion_context is not None
            and ingestion_context.clarification_text.strip()
            and task.chapter_id
        ):
            await draft_blocks.mark_blocks_stale_for_editorial_context(
                db,
                project_id=project_id,
                chapter_id=task.chapter_id,
                story_id=task.story_id,
            )
        answered_task_ids.append(task.id)
        if (
            task.target_slot == MEMOIR_STORY_NARRATION_TARGET
            and task.chapter_id
        ):
            chapters_to_reaudit.add(task.chapter_id)
        elif task.target_slot.startswith("semantic:") and task.chapter_id:
            chapters_to_semantic_reaudit.add(task.chapter_id)

    for chapter_id in sorted(chapters_to_reaudit | chapters_to_semantic_reaudit):
        await completion_refresh.enqueue_chapter_completion_refresh(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            include_semantic_audit=chapter_id in chapters_to_semantic_reaudit,
            ai_model=None,
            source="book_completion_answer",
        )

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
            relation_role=MemoirFollowupTaskAnswerRole.TARGET_ANSWER,
            answered_task_ids=tuple(answered_task_ids),
        )
    return FollowupCompletionResult()
