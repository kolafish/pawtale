from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole, MemoirSpeakerRole
from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance
from app.services.memoir.transcript_projection import current_utterance_text


def is_narrator_material(utterance: MemoirVoiceUtterance) -> bool:
    if utterance.chat_message is not None:
        return utterance.chat_message.role == MemoirChatMessageRole.USER.value
    payload = (
        utterance.provider_payload_json
        if isinstance(utterance.provider_payload_json, dict)
        else {}
    )
    return payload.get("speaker_role") != MemoirSpeakerRole.INTERVIEWER.value


def is_countable_material(
    utterance: MemoirVoiceUtterance,
    text: str | None = None,
) -> bool:
    content = (
        text
        if text is not None
        else current_utterance_text(utterance)
    )
    return bool(str(content or "").strip()) and is_narrator_material(utterance)


def narrator_material_clause():
    unlinked_speaker_role = (
        MemoirVoiceUtterance.provider_payload_json["speaker_role"].as_string()
    )
    return or_(
        MemoirVoiceUtterance.chat_message.has(
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value
        ),
        and_(
            MemoirVoiceUtterance.chat_message_id.is_(None),
            func.coalesce(
                unlinked_speaker_role,
                MemoirSpeakerRole.NARRATOR.value,
            )
            != MemoirSpeakerRole.INTERVIEWER.value,
        ),
    )


def is_ready_story_material(utterance: MemoirVoiceUtterance) -> bool:
    message = utterance.chat_message
    return is_countable_material(utterance) and (
        message is None or message.content_status in {
            MemoirChatContentStatus.FINAL.value, MemoirChatContentStatus.CORRECTED.value,
        }
    )


async def interviewer_lines(db: AsyncSession, message_ids: Iterable[str | None]) -> dict[str, str]:
    """For each narrator message, the AI interviewer's line right before it, if it was answering one."""
    ids = {message_id for message_id in message_ids if message_id}
    if not ids:
        return {}
    window = {
        "partition_by": MemoirChatMessage.voice_session_id,
        "order_by": (MemoirChatMessage.turn_index, MemoirChatMessage.created_at),
    }
    ordered = (
        select(
            MemoirChatMessage.id,
            func.lag(MemoirChatMessage.role).over(**window).label("previous_role"),
            func.lag(MemoirChatMessage.content_text).over(**window).label("previous_text"),
        )
        .where(MemoirChatMessage.voice_session_id.in_(
            select(MemoirChatMessage.voice_session_id).where(MemoirChatMessage.id.in_(ids))
        ))
        .subquery()
    )
    rows = await db.execute(
        select(ordered.c.id, ordered.c.previous_text).where(
            ordered.c.id.in_(ids),
            ordered.c.previous_role == MemoirChatMessageRole.ASSISTANT.value,
        )
    )
    return {message_id: text.strip() for message_id, text in rows if (text or "").strip()}
