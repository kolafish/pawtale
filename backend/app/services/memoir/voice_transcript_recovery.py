from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
import aiofiles
import aiofiles.os
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirSpeakerRole,
    MemoirTranscriptVersionType,
)
from app.models.memoir import MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_voice import MemoirVoiceTranscriptItemResponse
from app.services.memoir import voice_transcript_correction
from app.services.ai_credits import assert_ai_credit_available
from app.services.asr.billing import record_volc_streaming_asr_usage
from app.services.memoir.audio_files import audio_file_duration_seconds, audio_file_path
from app.services.asr.streaming import StreamingASRClient, StreamingASREvent
from app.services.memoir.material_roles import is_narrator_material
from app.services.memoir.project_ai_preferences import (
    load_project_transcript_polish_level,
)
from app.services.memoir.retrieval_source_index import schedule_source_indexing_job
from app.services.memoir.transcript_projection import current_utterance_text
from app.services.memoir.voice_transcripts import (
    get_voice_transcript,
    prune_transcript_versions_for_utterance,
)


ASR_RECOVERY_CHUNK_BYTES = 64 * 1024
ASR_RECOVERY_DRAIN_TIMEOUT_SECONDS = 60.0
ASR_RECOVERY_PCM_SAMPLE_RATE = 16000
ASR_RECOVERY_PCM_BYTES_PER_SAMPLE = 2
ASR_RECOVERY_PCM_SEGMENT_SECONDS = 55
ASR_RECOVERY_PCM_SEGMENT_BYTES = (
    ASR_RECOVERY_PCM_SAMPLE_RATE
    * ASR_RECOVERY_PCM_BYTES_PER_SAMPLE
    * ASR_RECOVERY_PCM_SEGMENT_SECONDS
)
StreamingASRClientFactory = Callable[[], StreamingASRClient]


async def recover_voice_transcript_from_audio(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    *,
    asr_client_factory: StreamingASRClientFactory,
    provider: str = "volc_streaming_asr",
    transcript_polish_level: str | None = None,
    allow_admin: bool = False,
) -> MemoirVoiceTranscriptItemResponse:
    utterance = await _load_recoverable_utterance(
        db,
        utterance_id,
        user_id,
        allow_admin=allow_admin,
    )
    if current_utterance_text(utterance):
        raise HTTPException(status_code=400, detail="这条讲述已经有转写文字")
    audio_file = utterance.audio_file
    if audio_file is None:
        raise HTTPException(status_code=400, detail="这条讲述没有可恢复的音频")
    path = audio_file_path(audio_file)
    if not path.exists():
        raise HTTPException(status_code=404, detail="音频文件不存在，无法恢复转写")

    duration_seconds = audio_file_duration_seconds(audio_file, path)
    if duration_seconds is None or duration_seconds <= 0:
        raise HTTPException(status_code=422, detail="音频时长无法确认，暂时不能恢复转写")
    await assert_ai_credit_available(user_id)
    try:
        recovered_event = await transcribe_audio_path_for_recovery(
            path,
            asr_client_factory=asr_client_factory,
        )
    except Exception as exc:
        await record_volc_streaming_asr_usage(
            audio_duration_seconds=duration_seconds,
            status="failed",
            error_type=type(exc).__name__,
        )
        raise
    await record_volc_streaming_asr_usage(
        audio_duration_seconds=duration_seconds,
        status="succeeded",
    )
    await _apply_recovered_transcript(
        db,
        utterance,
        recovered_event,
        provider=provider,
        transcript_polish_level=transcript_polish_level,
    )
    return await get_voice_transcript(
        db,
        utterance.id,
        user_id,
        allow_admin=allow_admin,
    )


async def transcribe_audio_path_for_recovery(
    path: Path,
    *,
    asr_client_factory: StreamingASRClientFactory,
) -> StreamingASREvent:
    segments = await _recovery_audio_segments(path)
    if len(segments) == 1:
        start_byte, byte_count = segments[0]
        return await _transcribe_audio_segment_for_recovery(
            path,
            start_byte=start_byte,
            byte_count=byte_count,
            asr_client_factory=asr_client_factory,
        )

    recovered_events: list[StreamingASREvent] = []
    for segment_index, (start_byte, byte_count) in enumerate(segments):
        try:
            segment_event = await _transcribe_audio_segment_for_recovery(
                path,
                start_byte=start_byte,
                byte_count=byte_count,
                asr_client_factory=asr_client_factory,
            )
        except HTTPException as exc:
            if exc.status_code == 422:
                continue
            raise
        recovered_events.append(
            _offset_segment_event(
                segment_event,
                segment_index=segment_index,
                start_byte=start_byte,
                byte_count=byte_count,
            )
        )

    if not recovered_events:
        raise HTTPException(status_code=422, detail="没有识别到这段录音的文字")
    return _merge_segment_events(recovered_events)


async def _transcribe_audio_segment_for_recovery(
    path: Path,
    *,
    start_byte: int,
    byte_count: int,
    asr_client_factory: StreamingASRClientFactory,
) -> StreamingASREvent:
    asr_client = asr_client_factory()
    final_events: list[StreamingASREvent] = []
    latest_text_event: StreamingASREvent | None = None
    collect_task: asyncio.Task[None] | None = None

    async def collect_events() -> None:
        nonlocal latest_text_event
        async for event in asr_client.events():
            text = (event.text or "").strip()
            if text:
                latest_text_event = event
            if event.type == "final" and text:
                final_events.append(event)
            if event.type == "error":
                raise HTTPException(status_code=502, detail="语音识别服务暂时不可用")

    try:
        await asr_client.start()
        collect_task = asyncio.create_task(collect_events())
        async with aiofiles.open(path, "rb") as file:
            await file.seek(start_byte)
            remaining = byte_count
            while remaining > 0:
                chunk = await file.read(min(ASR_RECOVERY_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await asr_client.send_audio(chunk)
        await asr_client.finish()
        await asyncio.wait_for(asyncio.shield(collect_task), timeout=ASR_RECOVERY_DRAIN_TIMEOUT_SECONDS)
    except HTTPException:
        raise
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="语音识别超时，请稍后重试") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="语音识别服务暂时不可用") from exc
    finally:
        if collect_task is not None and not collect_task.done():
            collect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collect_task
        with contextlib.suppress(Exception):
            await asr_client.close()

    recovered_event = final_events[-1] if final_events else latest_text_event
    if recovered_event is None or not (recovered_event.text or "").strip():
        raise HTTPException(status_code=422, detail="没有识别到这段录音的文字")
    return recovered_event


async def _recovery_audio_segments(path: Path) -> list[tuple[int, int]]:
    stat = await aiofiles.os.stat(path)
    size_bytes = int(stat.st_size)
    if size_bytes <= 0:
        return [(0, 0)]
    if path.suffix.lower() != ".pcm" or size_bytes <= ASR_RECOVERY_PCM_SEGMENT_BYTES:
        return [(0, size_bytes)]

    segment_bytes = _pcm_aligned_segment_bytes(ASR_RECOVERY_PCM_SEGMENT_BYTES)
    segments: list[tuple[int, int]] = []
    start_byte = 0
    while start_byte < size_bytes:
        byte_count = min(segment_bytes, size_bytes - start_byte)
        if start_byte + byte_count < size_bytes:
            byte_count = _pcm_aligned_segment_bytes(byte_count)
        if byte_count <= 0:
            byte_count = size_bytes - start_byte
        segments.append((start_byte, byte_count))
        start_byte += byte_count
    return segments


def _pcm_aligned_segment_bytes(value: int) -> int:
    return max(ASR_RECOVERY_PCM_BYTES_PER_SAMPLE, value - (value % ASR_RECOVERY_PCM_BYTES_PER_SAMPLE))


def _offset_segment_event(
    event: StreamingASREvent,
    *,
    segment_index: int,
    start_byte: int,
    byte_count: int,
) -> StreamingASREvent:
    offset_ms = _pcm_byte_offset_ms(start_byte)
    return StreamingASREvent(
        type=event.type,
        text=(event.text or "").strip(),
        start_ms=(event.start_ms + offset_ms) if event.start_ms is not None else offset_ms,
        end_ms=(
            event.end_ms + offset_ms
            if event.end_ms is not None
            else _pcm_byte_offset_ms(start_byte + byte_count)
        ),
        raw={
            "segment_index": segment_index,
            "start_byte": start_byte,
            "byte_count": byte_count,
            "offset_ms": offset_ms,
            "provider_payload": event.raw,
        },
        error=event.error,
    )


def _pcm_byte_offset_ms(byte_offset: int) -> int:
    bytes_per_second = ASR_RECOVERY_PCM_SAMPLE_RATE * ASR_RECOVERY_PCM_BYTES_PER_SAMPLE
    return round(byte_offset * 1000 / bytes_per_second)


def _merge_segment_events(events: list[StreamingASREvent]) -> StreamingASREvent:
    texts = [(event.text or "").strip() for event in events if (event.text or "").strip()]
    text = "\n".join(texts)
    return StreamingASREvent(
        type="final",
        text=text,
        start_ms=events[0].start_ms,
        end_ms=events[-1].end_ms,
        raw={
            "source": "segmented_transcript_recovery",
            "segment_count": len(events),
            "segments": [event.raw for event in events],
        },
    )


async def _load_recoverable_utterance(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    *,
    allow_admin: bool,
) -> MemoirVoiceUtterance:
    filters = [MemoirVoiceUtterance.id == utterance_id]
    if not allow_admin:
        filters.append(Project.user_id == user_id)
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
        .options(
            selectinload(MemoirVoiceUtterance.audio_file),
            selectinload(MemoirVoiceUtterance.chat_message),
            selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.project),
        )
    )
    utterance = result.scalar_one_or_none()
    if not utterance:
        raise HTTPException(status_code=404, detail="讲述记录不存在")
    return utterance


async def _apply_recovered_transcript(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    event: StreamingASREvent,
    *,
    provider: str,
    transcript_polish_level: str | None,
) -> None:
    content = (event.text or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="没有识别到这段录音的文字")

    voice_session = utterance.voice_session
    transcript_version = MemoirTranscriptVersion(
        voice_session_id=voice_session.id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.ASR_RAW.value,
        content=content,
        provider=provider,
        created_by=MemoirCreatedBy.ASR.value,
    )
    db.add(transcript_version)
    await db.flush()

    chat_role = _chat_role_for_utterance(utterance)
    speaker_role = _speaker_role_for_utterance(utterance)
    message = utterance.chat_message
    if message is None:
        message = MemoirChatMessage(
            project_id=voice_session.project_id,
            voice_session_id=voice_session.id,
            role=chat_role,
            turn_index=await _next_turn_index(db, voice_session.id),
        )
        db.add(message)
        await db.flush()
        utterance.chat_message_id = message.id
        utterance.chat_message = message

    message.content_text = content
    message.content_status = MemoirChatContentStatus.FINAL.value
    message.audio_file_id = utterance.audio_file_id
    message.transcript_version_id = transcript_version.id
    message.metadata_json = {
        **(message.metadata_json or {}),
        "source": "transcript_recovery",
        "provider": provider,
        "speaker_role": speaker_role,
        "interview_type": voice_session.interview_type,
        "auto_corrected": False,
        "correction_status": "skipped" if speaker_role == MemoirSpeakerRole.INTERVIEWER.value else "pending",
        "asr_transcript_version_id": transcript_version.id,
    }

    utterance.transcript_raw = content
    utterance.provider = provider
    utterance.start_ms = event.start_ms if event.start_ms is not None else utterance.start_ms
    utterance.end_ms = event.end_ms if event.end_ms is not None else utterance.end_ms
    utterance.provider_payload_json = {
        "source": "transcript_recovery",
        "provider": provider,
        "provider_payload": event.raw,
        "previous_payload": utterance.provider_payload_json or {},
    }
    db.add(utterance)
    db.add(message)
    await prune_transcript_versions_for_utterance(
        db,
        utterance_id=utterance.id,
        version_type=MemoirTranscriptVersionType.ASR_RAW,
    )
    if speaker_role == MemoirSpeakerRole.NARRATOR.value:
        polish_level = await load_project_transcript_polish_level(
            db,
            voice_session.project_id,
            transcript_polish_level,
        )
        await voice_transcript_correction.schedule_transcript_correction_job(
            db,
            project_id=voice_session.project_id,
            utterance_id=utterance.id,
            voice_session_id=voice_session.id,
            raw_transcript_version_id=transcript_version.id,
            transcript_polish_level=polish_level,
        )
        await schedule_source_indexing_job(
            db,
            project_id=voice_session.project_id,
            utterance=utterance,
            source="transcript_recovery",
        )
    await db.commit()


def _chat_role_for_utterance(utterance: MemoirVoiceUtterance) -> str:
    return (
        MemoirChatMessageRole.USER.value
        if is_narrator_material(utterance)
        else MemoirChatMessageRole.INTERVIEWER.value
    )


def _speaker_role_for_utterance(utterance: MemoirVoiceUtterance) -> str:
    return (
        MemoirSpeakerRole.NARRATOR.value
        if is_narrator_material(utterance)
        else MemoirSpeakerRole.INTERVIEWER.value
    )


async def _next_turn_index(db: AsyncSession, voice_session_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirChatMessage.turn_index)).where(MemoirChatMessage.voice_session_id == voice_session_id)
    )
    return int(value or 0) + 1
