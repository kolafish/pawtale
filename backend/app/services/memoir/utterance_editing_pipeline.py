from __future__ import annotations

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


async def claim_transcript_mutation_guard(
    db: AsyncSession,
    *,
    utterance: MemoirVoiceUtterance,
    expected: TranscriptMutationGuard,
) -> bool:
    locked_utterance = (
        await db.execute(
            select(
                MemoirVoiceUtterance.transcript_raw,
                MemoirVoiceUtterance.transcript_edited,
                MemoirVoiceUtterance.chat_message_id,
            )
            .where(MemoirVoiceUtterance.id == utterance.id)
            .with_for_update()
        )
    ).one_or_none()
    if locked_utterance is None:
        return False
    locked_text = (locked_utterance.transcript_edited or "").strip() or (
        locked_utterance.transcript_raw or ""
    ).strip()
    if locked_utterance.chat_message_id is None:
        return False
    lines = await interviewer_lines(db, [locked_utterance.chat_message_id], lock=True)
    question = lines.get(locked_utterance.chat_message_id, "")
    if _cleanup_input_hash(locked_text, question) != expected.source_text_hash:
        return False

    locked_message = (
        await db.execute(
            select(
                MemoirChatMessage.content_text,
                MemoirChatMessage.transcript_version_id,
            )
            .where(
                MemoirChatMessage.id == locked_utterance.chat_message_id,
            )
            .with_for_update()
        )
    ).one_or_none()
    if locked_message is None:
        return False
    matches = (
        _cleanup_input_hash(locked_message.content_text, question) == expected.source_text_hash
        and locked_message.transcript_version_id == expected.transcript_version_id
    )
    if not matches:
        return False
    message = utterance.chat_message
    if message is None:
        return False
    await db.refresh(
        message,
        attribute_names=[
            "content_text",
            "content_status",
            "transcript_version_id",
            "metadata_json",
        ],
    )
    return True


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
