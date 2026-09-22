from __future__ import annotations

import logging
import math

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import AudioFile, MemoirChatMessage, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_voice import MemoirAssistantTTSRequest, MemoirAssistantTTSResponse
from app.services.memoir import voice_tts
from app.services.memoir.audio_files import delete_audio_file_paths, discard_staged_audio, stage_audio_bytes
from app.services.memoir.tts_alignment import (
    alignment_segments_from_json,
    alignment_segments_to_json,
)
from app.services.memoir.voice_tts_codec import effective_tts_speed_ratio
from app.services.ai_costs import estimate_text_cost
from app.services.ai_usage_records import record_ai_usage
from app.services.memoir.voice_tts_types import (
    TTSClient,
    TTSResult,
    TTS_ALIGNMENT_METADATA_KEY,
    TTS_SPEED_RATIO_METADATA_KEY,
    TTS_TIMING_SOURCE_METADATA_KEY,
    TTS_UNAVAILABLE_MESSAGE,
)


logger = logging.getLogger(__name__)


async def _record_tts_usage(
    *,
    provider: str,
    model: str,
    character_count: int,
    status: str,
    error_type: str | None = None,
) -> None:
    await record_ai_usage(
        provider=provider,
        model=model,
        status=status,
        cost=estimate_text_cost(
            provider=provider,
            model=model,
            character_count=character_count,
        ),
        input_characters=character_count,
        error_type=error_type,
    )


async def _load_assistant_message(
    db: AsyncSession,
    voice_session_id: str,
    message_id: str,
    user_id: str,
) -> MemoirChatMessage:
    result = await db.execute(
        select(MemoirChatMessage)
        .join(Project, MemoirChatMessage.project_id == Project.id)
        .where(
            MemoirChatMessage.id == message_id,
            MemoirChatMessage.voice_session_id == voice_session_id,
            Project.user_id == user_id,
        )
        .options(selectinload(MemoirChatMessage.audio_file))
    )
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="聊天消息不存在")
    if message.role != MemoirChatMessageRole.ASSISTANT.value:
        raise HTTPException(status_code=400, detail="只有 AI 回应可以生成语音")
    if not message.content_text.strip():
        raise HTTPException(status_code=400, detail="AI 回应内容为空")
    return message


async def _load_existing_audio(db: AsyncSession, message: MemoirChatMessage) -> AudioFile | None:
    if not message.audio_file_id:
        return None
    result = await db.execute(select(AudioFile).where(AudioFile.id == message.audio_file_id))
    return result.scalar_one_or_none()


async def _audio_file_is_referenced(db: AsyncSession, audio_file_id: str) -> bool:
    queries = [
        select(MemoirChatMessage.id).where(MemoirChatMessage.audio_file_id == audio_file_id).limit(1),
        select(MemoirVoiceUtterance.id).where(MemoirVoiceUtterance.audio_file_id == audio_file_id).limit(1),
    ]
    for query in queries:
        if await db.scalar(query):
            return True
    return False


async def _delete_unreferenced_audio_file(db: AsyncSession, audio_file: AudioFile | None) -> None:
    if audio_file is None:
        return
    if await _audio_file_is_referenced(db, audio_file.id):
        return
    await db.delete(audio_file)
    await db.commit()
    try:
        await delete_audio_file_paths([audio_file])
    except Exception:
        logger.warning(
            "Failed to delete replaced TTS audio file audio_file_id=%s",
            audio_file.id,
            exc_info=True,
        )


def _message_alignment(message: MemoirChatMessage) -> list[dict[str, object]]:
    metadata = message.metadata_json or {}
    return alignment_segments_to_json(alignment_segments_from_json(metadata.get(TTS_ALIGNMENT_METADATA_KEY)))


def _message_has_alignment(message: MemoirChatMessage) -> bool:
    metadata = message.metadata_json or {}
    return bool(alignment_segments_from_json(metadata.get(TTS_ALIGNMENT_METADATA_KEY)))


def _effective_tts_voice(client: TTSClient, requested_voice: str | None) -> str:
    resolver = getattr(client, "effective_voice", None)
    if callable(resolver):
        return str(resolver(requested_voice) or "").strip()
    return (requested_voice or getattr(client, "default_voice", "") or "").strip()


def _can_reuse_existing_audio(
    message: MemoirChatMessage,
    client: TTSClient,
    *,
    voice: str,
    speed_ratio: float,
) -> bool:
    metadata = message.metadata_json or {}
    if metadata.get("tts_provider") != client.provider_name:
        return False
    if metadata.get("tts_model") != client.model_name:
        return False
    if str(metadata.get("tts_voice") or "") != voice:
        return False
    if effective_tts_speed_ratio(metadata.get(TTS_SPEED_RATIO_METADATA_KEY)) != speed_ratio:
        return False
    return TTS_TIMING_SOURCE_METADATA_KEY in metadata or _message_has_alignment(message)


def _tts_timing_metadata(result: TTSResult) -> dict[str, object]:
    return {
        TTS_ALIGNMENT_METADATA_KEY: alignment_segments_to_json(result.alignment),
        TTS_TIMING_SOURCE_METADATA_KEY: result.provider if result.alignment else None,
        "tts_duration_seconds": result.duration_seconds,
        TTS_SPEED_RATIO_METADATA_KEY: result.speed_ratio,
    }


def _message_duration_seconds(message: MemoirChatMessage, audio_file: AudioFile | None = None) -> float | None:
    value = (message.metadata_json or {}).get("tts_duration_seconds")
    try:
        duration = float(value)
    except (TypeError, ValueError):
        duration = None
    if duration is not None and duration >= 0:
        return duration
    if audio_file and audio_file.duration_seconds is not None:
        return float(audio_file.duration_seconds)
    return None


def _tts_response(
    message: MemoirChatMessage,
    audio_file: AudioFile,
    *,
    provider: str,
    model: str,
) -> MemoirAssistantTTSResponse:
    metadata = message.metadata_json or {}
    return MemoirAssistantTTSResponse(
        message=message,
        audio_file_id=audio_file.id,
        provider=provider,
        model=model,
        mime_type=audio_file.mime_type,
        duration_seconds=_message_duration_seconds(message, audio_file),
        alignment_provider=str(metadata.get(TTS_TIMING_SOURCE_METADATA_KEY) or "") or None,
        alignment=_message_alignment(message),
    )


async def synthesize_assistant_message(
    db: AsyncSession,
    voice_session_id: str,
    message_id: str,
    user_id: str,
    req: MemoirAssistantTTSRequest,
    tts_client: TTSClient | None = None,
) -> MemoirAssistantTTSResponse:
    message = await _load_assistant_message(db, voice_session_id, message_id, user_id)
    existing_audio = await _load_existing_audio(db, message)
    client = tts_client or voice_tts.get_tts_client()
    effective_voice = _effective_tts_voice(client, req.voice)
    effective_speed_ratio = effective_tts_speed_ratio(req.speed_ratio)
    if (
        existing_audio
        and not req.force_regenerate
        and _can_reuse_existing_audio(message, client, voice=effective_voice, speed_ratio=effective_speed_ratio)
    ):
        return _tts_response(
            message,
            existing_audio,
            provider=str(message.metadata_json.get("tts_provider") or "stored"),
            model=str(message.metadata_json.get("tts_model") or ""),
        )

    text = message.content_text.strip()
    try:
        result = await client.synthesize(text, voice=req.voice, speed_ratio=req.speed_ratio)
    except HTTPException as exc:
        await _record_tts_usage(
            provider=client.provider_name,
            model=client.model_name,
            character_count=len(text),
            status="failed",
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        await _record_tts_usage(
            provider=client.provider_name,
            model=client.model_name,
            character_count=len(text),
            status="failed",
            error_type=type(exc).__name__,
        )
        logger.exception("TTS synthesis failed provider=%s model=%s", client.provider_name, client.model_name)
        raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc
    await _record_tts_usage(
        provider=result.provider,
        model=result.model,
        character_count=len(text),
        status="succeeded",
    )

    staged_audio = await stage_audio_bytes(
        db,
        user_id,
        result.audio,
        mime_type=result.mime_type,
        extension=result.extension,
        duration_seconds=math.ceil(result.duration_seconds) if result.duration_seconds is not None else None,
    )
    try:
        message.audio_file_id = staged_audio.audio_file.id
        message.metadata_json = {
            **(message.metadata_json or {}),
            "tts_provider": result.provider,
            "tts_model": result.model,
            "tts_voice": result.voice,
            "tts_mime_type": result.mime_type,
            **_tts_timing_metadata(result),
        }
        db.add(message)
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio(staged_audio)
        raise

    await db.refresh(message)
    await db.refresh(staged_audio.audio_file)
    if existing_audio and existing_audio.id != staged_audio.audio_file.id:
        try:
            await _delete_unreferenced_audio_file(db, existing_audio)
        except Exception:
            await db.rollback()
            logger.warning(
                "Failed to delete replaced TTS audio row audio_file_id=%s",
                existing_audio.id,
                exc_info=True,
            )
    return _tts_response(
        message,
        staged_audio.audio_file,
        provider=result.provider,
        model=result.model,
    )
