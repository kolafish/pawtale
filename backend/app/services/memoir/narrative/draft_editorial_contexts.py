from __future__ import annotations

from hashlib import sha256
import json

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerKind,
    MemoirFollowupTaskStatus,
    MemoirFollowupTaskAnswerRole,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirFollowupTask,
    MemoirFollowupTaskAnswer,
    MemoirUtteranceInterviewAssignment,
)
from app.services.memoir.followup_answer_parts import validate_resolved_answer_parts
from app.services.memoir.narrative.draft_block_ai import (
    DraftBlockEditorialContextInput,
)


def _utterance_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (
        utterance.transcript_raw or ""
    ).strip()


async def load_chapter_editorial_contexts(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[DraftBlockEditorialContextInput]:
    rows = (
        await db.execute(
            select(
                MemoirFollowupTaskAnswer,
                MemoirFollowupTask,
                MemoirVoiceUtterance,
                MemoirChatMessage.transcript_version_id,
            )
            .join(
                MemoirFollowupTask,
                and_(
                    MemoirFollowupTask.project_id
                    == MemoirFollowupTaskAnswer.project_id,
                    MemoirFollowupTask.id == MemoirFollowupTaskAnswer.task_id,
                ),
            )
            .join(
                MemoirVoiceUtterance,
                MemoirVoiceUtterance.id
                == MemoirFollowupTaskAnswer.utterance_id,
            )
            .join(
                MemoirUtteranceInterviewAssignment,
                MemoirUtteranceInterviewAssignment.utterance_id
                == MemoirFollowupTaskAnswer.utterance_id,
            )
            .join(
                MemoirVoiceSession,
                MemoirVoiceSession.id == MemoirVoiceUtterance.voice_session_id,
            )
            .outerjoin(
                MemoirChatMessage,
                and_(
                    MemoirChatMessage.id == MemoirVoiceUtterance.chat_message_id,
                    MemoirChatMessage.project_id == project_id,
                ),
            )
            .where(
                MemoirFollowupTaskAnswer.project_id == project_id,
                MemoirUtteranceInterviewAssignment.followup_task_id
                == MemoirFollowupTaskAnswer.task_id,
                MemoirFollowupTaskAnswer.result_role
                == MemoirFollowupTaskAnswerRole.TARGET_ANSWER.value,
                MemoirFollowupTaskAnswer.interpretation_status
                == MemoirFollowupAnswerInterpretationStatus.RESOLVED.value,
                MemoirFollowupTask.project_id == project_id,
                MemoirFollowupTask.chapter_id == chapter_id,
                MemoirFollowupTask.status == MemoirFollowupTaskStatus.ANSWERED.value,
                MemoirVoiceSession.project_id == project_id,
            )
            .order_by(
                MemoirFollowupTaskAnswer.created_at,
                MemoirFollowupTaskAnswer.id,
            )
        )
    ).all()

    contexts: list[DraftBlockEditorialContextInput] = []
    for relation, task, utterance, transcript_version_id in rows:
        try:
            answer_kind = MemoirFollowupAnswerKind(relation.answer_kind)
            validate_resolved_answer_parts(
                source_text=_utterance_text(utterance),
                answer_kind=answer_kind,
                direct_narrative_text=relation.direct_narrative_text,
                clarification_text=relation.clarification_text,
                fact_resolution_text=relation.fact_resolution_text,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "completion answer semantics no longer match the current transcript; "
                "confirm the answer purpose before generating draft blocks"
            ) from exc

        clarification_text = relation.clarification_text.strip()
        fact_resolution_text = relation.fact_resolution_text.strip()
        if not clarification_text and not fact_resolution_text:
            continue
        contexts.append(
            DraftBlockEditorialContextInput(
                relation_id=relation.id,
                task_id=task.id,
                utterance_id=utterance.id,
                transcript_version_id=transcript_version_id,
                story_id=task.story_id,
                question=task.question,
                purpose=task.purpose,
                answer_kind=answer_kind,
                clarification_text=clarification_text,
                fact_resolution_text=fact_resolution_text,
            )
        )
    return contexts


def editorial_context_signature(
    contexts: list[DraftBlockEditorialContextInput],
) -> str:
    encoded = json.dumps(
        [context.model_dump(mode="json") for context in contexts],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def editorial_contexts_for_node(
    contexts: list[DraftBlockEditorialContextInput],
    *,
    story_ids: set[str],
) -> list[DraftBlockEditorialContextInput]:
    return [
        context
        for context in contexts
        if context.story_id is None or context.story_id in story_ids
    ]
