from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import (
    MemoirChatMessage,
    MemoirSectionMaterial,
    MemoirVoiceUtterance,
)
from app.services.memoir import narrator_profile, sections, story_tree_growth
from app.services.memoir.material_roles import interviewer_lines
from app.services.memoir.semantic_state import semantic_json_signature
from app.services.memoir.retrieval_source_index import schedule_source_indexing_job
from app.services.memoir.narrative.framework_matching import (
    schedule_framework_matching_job,
)
from app.services.memoir.transcript_projection import (
    current_transcript_version_id,
    current_utterance_text,
)


@dataclass(frozen=True)
class TranscriptMutationGuard:
    source_text_hash: str
    transcript_version_id: str | None


def transcript_mutation_guard(
    utterance: MemoirVoiceUtterance, *, interviewer_line: str = "",
) -> TranscriptMutationGuard:
    return TranscriptMutationGuard(
        source_text_hash=_cleanup_input_hash(current_utterance_text(utterance), interviewer_line),
        transcript_version_id=current_transcript_version_id(utterance),
    )


def _cleanup_input_hash(text: str, question: str) -> str:
    # The existing API field is an opaque preview token echoed by clients.
    # Include interpretation context so older/context-stale previews conflict.
    return semantic_json_signature({"text": text.strip(), "question": question.strip(), "engine": "cleanup_input_v2"})


async def claim_transcript_mutation_guards(
    db: AsyncSession, *, utterances: Sequence[MemoirVoiceUtterance],
    expected: Mapping[str, TranscriptMutationGuard],
) -> bool:
    # All source locks precede shared question locks; disjoint batches may still
    # share a question. Stable ordering also protects overlapping source batches.
    locked = {row.id: row for row in (await db.execute(select(
        MemoirVoiceUtterance.id, MemoirVoiceUtterance.transcript_raw,
        MemoirVoiceUtterance.transcript_edited, MemoirVoiceUtterance.chat_message_id,
    ).where(MemoirVoiceUtterance.id.in_([item.id for item in utterances]))
        .order_by(MemoirVoiceUtterance.id).with_for_update())).all()}
    if len(locked) != len(utterances) or any(row.chat_message_id is None for row in locked.values()):
        return False
    message_ids = [row.chat_message_id for row in locked.values()]
    lines = await interviewer_lines(db, message_ids, lock=True)
    # The resolver locked answers with their possible questions. Refresh all
    # answer content and metadata once, rather than re-locking/refreshing each.
    messages = {message.id: message for message in await db.scalars(select(MemoirChatMessage).where(
        MemoirChatMessage.id.in_(message_ids),
    ).execution_options(populate_existing=True))}
    for utterance in utterances:
        row = locked[utterance.id]
        message = messages.get(row.chat_message_id)
        guard = expected[utterance.id]
        question = lines.get(row.chat_message_id, "")
        locked_text = (row.transcript_edited or "").strip() or (row.transcript_raw or "").strip()
        if (row.chat_message_id != utterance.chat_message_id or message is None
            or _cleanup_input_hash(locked_text, question) != guard.source_text_hash
            or _cleanup_input_hash(message.content_text, question) != guard.source_text_hash
            or message.transcript_version_id != guard.transcript_version_id):
            return False
    return True


async def claim_transcript_mutation_guard(
    db: AsyncSession, *, utterance: MemoirVoiceUtterance, expected: TranscriptMutationGuard,
) -> bool:
    return await claim_transcript_mutation_guards(db, utterances=[utterance], expected={utterance.id: expected})


async def schedule_ready_utterance_background_work(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    source: str,
    ai_model: str | None = None,
) -> None:
    text = current_utterance_text(utterance)
    if not text:
        return
    await schedule_source_indexing_job(
        db,
        project_id=project_id,
        utterance=utterance,
        source=source,
    )
    await schedule_framework_matching_job(
        db,
        project_id=project_id,
        material_ids=[utterance.id],
        ai_model=ai_model,
    )

    existing = await db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == utterance.id,
        )
    )
    transcript_version_id = current_transcript_version_id(utterance)
    if existing is not None:
        if transcript_version_id:
            await sections.refresh_material_transcript_version(
                db,
                project_id=project_id,
                utterance_id=utterance.id,
                transcript_version_id=transcript_version_id,
            )
    await story_tree_growth.schedule_story_tree_growth(
        db, project_id=project_id, voice_session_id=utterance.voice_session_id, source=source,
    )
    if source != "share_response":
        await narrator_profile.schedule_narrator_profile(db, utterance=utterance)
