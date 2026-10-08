from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_errors import ApplicationError
from app.config import get_settings
from app.models.memoir import AudioFile
from app.schemas.memoir_book import MemoirBookAudioResponse
from app.services.memoir.audio_files import StagedAudioFile, discard_staged_audio, stage_audio_bytes
from app.services.memoir.tts_alignment import (
    TTSAlignmentSegment,
    alignment_segments_from_json,
    alignment_segments_to_json,
)
from app.services.tts_stepfun import StepFunTTSClient
from app.services.memoir.voice_tts_types import TTSClient, TTS_UNAVAILABLE_MESSAGE
from app.services.ai_costs import estimate_text_cost
from app.services.ai_usage_records import record_ai_usage


MAX_TTS_CHARS_PER_CHUNK = 900


def get_tts_client() -> StepFunTTSClient:
    return StepFunTTSClient(get_settings())


@dataclass(frozen=True)
class TTSChunk:
    text: str
    text_start: int
    text_end: int


@dataclass(frozen=True)
class BookAudioSynthesis:
    audio_files: list[AudioFile]
    audio_file_durations_seconds: list[float | None]
    staged_audio: list[StagedAudioFile]
    provider: str
    model: str
    mime_type: str
    source_text_hash: str
    duration_seconds: float | None
    alignment: list[TTSAlignmentSegment]


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_tts_text(text: str, *, max_chars: int = MAX_TTS_CHARS_PER_CHUNK) -> list[TTSChunk]:
    content = text.strip()
    if not content:
        raise HTTPException(status_code=400, detail="朗读内容为空")
    positioned_chunks: list[TTSChunk] = []
    start = 0
    while start < len(content):
        while start < len(content) and content[start].isspace():
            start += 1
        if start >= len(content):
            break
        end = min(len(content), start + max_chars)
        if end < len(content):
            window = content[start:end]
            split_at = max(window.rfind("。"), window.rfind("，"), window.rfind("\n"))
            if split_at > 0:
                end = start + split_at + 1
        raw_chunk = content[start:end]
        chunk = raw_chunk.strip()
        if chunk:
            leading_whitespace = len(raw_chunk) - len(raw_chunk.lstrip())
            text_start = start + leading_whitespace
            text_end = text_start + len(chunk)
            positioned_chunks.append(TTSChunk(text=chunk, text_start=text_start, text_end=text_end))
        start = max(end, start + 1)
    return positioned_chunks


def cached_audio_response(
    metadata_json: dict,
    *,
    cache_key: str,
    text_hash: str,
    voice: str | None,
    speed_ratio: float,
    force_regenerate: bool,
    tts_client: TTSClient | None = None,
) -> MemoirBookAudioResponse | None:
    cache = metadata_json.get(cache_key)
    if force_regenerate or not isinstance(cache, dict):
        return None
    client = tts_client or get_tts_client()
    audio_file_ids = cache.get("audio_file_ids")
    if (
        cache.get("provider") == client.provider_name
        and cache.get("model") == client.model_name
        and cache.get("source_text_hash") == text_hash
        and (cache.get("voice") or None) == (voice or None)
        and _cache_speed_ratio(cache.get("speed_ratio")) == speed_ratio
        and isinstance(audio_file_ids, list)
        and all(isinstance(item, str) for item in audio_file_ids)
        and audio_file_ids
    ):
        alignment = alignment_segments_to_json(alignment_segments_from_json(cache.get("alignment")))
        return MemoirBookAudioResponse(
            audio_file_ids=audio_file_ids,
            audio_file_durations_seconds=_cache_audio_file_durations_seconds(
                cache.get("audio_file_durations_seconds"),
                len(audio_file_ids),
            ),
            provider=str(cache.get("provider") or "stored"),
            model=str(cache.get("model") or ""),
            mime_type=str(cache.get("mime_type") or "audio/mpeg"),
            chunk_count=int(cache.get("chunk_count") or len(audio_file_ids)),
            source_text_hash=text_hash,
            duration_seconds=_cache_duration_seconds(cache.get("duration_seconds")),
            alignment_provider=str(cache.get("alignment_provider") or "") or None,
            alignment=alignment,
        )
    return None


def _cache_speed_ratio(value: object) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(ratio):
        return 1.0
    return max(0.5, min(2.0, round(ratio, 2)))


def effective_tts_speed_ratio(value: object) -> float:
    return _cache_speed_ratio(value)


def _cache_duration_seconds(value: object) -> float | None:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


def _cache_audio_file_durations_seconds(value: object, count: int) -> list[float | None]:
    if not isinstance(value, list):
        return []
    durations = [_cache_duration_seconds(item) for item in value[:count]]
    if len(durations) != count:
        return []
    return durations


def audio_cache_payload(
    synthesis: BookAudioSynthesis,
    *,
    voice: str | None,
    speed_ratio: float,
) -> dict[str, object]:
    return {
        "audio_file_ids": [audio_file.id for audio_file in synthesis.audio_files],
        "audio_file_durations_seconds": synthesis.audio_file_durations_seconds,
        "provider": synthesis.provider,
        "model": synthesis.model,
        "mime_type": synthesis.mime_type,
        "chunk_count": len(synthesis.audio_files),
        "source_text_hash": synthesis.source_text_hash,
        "voice": voice,
        "speed_ratio": speed_ratio,
        "duration_seconds": synthesis.duration_seconds,
        "alignment_provider": synthesis.provider if synthesis.alignment else None,
        "alignment": alignment_segments_to_json(synthesis.alignment),
    }


def audio_response(synthesis: BookAudioSynthesis) -> MemoirBookAudioResponse:
    return MemoirBookAudioResponse(
        audio_file_ids=[audio_file.id for audio_file in synthesis.audio_files],
        audio_file_durations_seconds=synthesis.audio_file_durations_seconds,
        provider=synthesis.provider,
        model=synthesis.model,
        mime_type=synthesis.mime_type,
        chunk_count=len(synthesis.audio_files),
        source_text_hash=synthesis.source_text_hash,
        duration_seconds=synthesis.duration_seconds,
        alignment_provider=synthesis.provider if synthesis.alignment else None,
        alignment=alignment_segments_to_json(synthesis.alignment),
    )


async def discard_staged_audio_files(staged_audio: list[StagedAudioFile]) -> None:
    for staged in staged_audio:
        await discard_staged_audio(staged)


async def synthesize_text_audio(
    db: AsyncSession,
    *,
    user_id: str,
    text: str,
    voice: str | None = None,
    speed_ratio: float | None = None,
    tts_client: TTSClient | None = None,
) -> BookAudioSynthesis:
    chunks = split_tts_text(text)
    client = tts_client or get_tts_client()
    staged_audio: list[StagedAudioFile] = []
    audio_files: list[AudioFile] = []
    audio_file_durations_seconds: list[float | None] = []
    alignment: list[TTSAlignmentSegment] = []
    provider = client.provider_name
    model = client.model_name
    mime_type = ""
    elapsed_ms = 0
    duration_seconds: float | None = None
    try:
        for chunk in chunks:
            try:
                result = await client.synthesize(chunk.text, voice=voice, speed_ratio=speed_ratio)
            except Exception as exc:
                await record_ai_usage(
                    provider=client.provider_name,
                    model=client.model_name,
                    status="failed",
                    cost=estimate_text_cost(
                        provider=client.provider_name,
                        model=client.model_name,
                        character_count=len(chunk.text),
                        text=chunk.text,
                    ),
                    input_characters=len(chunk.text),
                    error_type=type(exc).__name__,
                )
                raise
            await record_ai_usage(
                provider=result.provider,
                model=result.model,
                status="succeeded",
                cost=estimate_text_cost(
                    provider=result.provider,
                    model=result.model,
                    character_count=len(chunk.text),
                    text=chunk.text,
                ),
                input_characters=len(chunk.text),
            )
            mime_type = result.mime_type
            provider = result.provider
            model = result.model
            chunk_duration_ms = _chunk_duration_ms(result.alignment, result.duration_seconds)
            chunk_duration_seconds = chunk_duration_ms / 1000 if chunk_duration_ms > 0 else None
            for segment in result.alignment:
                alignment.append(
                    TTSAlignmentSegment(
                        text=segment.text,
                        start_ms=elapsed_ms + segment.start_ms,
                        end_ms=elapsed_ms + segment.end_ms,
                        text_start=chunk.text_start + segment.text_start,
                        text_end=chunk.text_start + segment.text_end,
                        confidence=segment.confidence,
                    )
                )
            staged = await stage_audio_bytes(
                db,
                user_id,
                result.audio,
                mime_type=result.mime_type,
                extension=result.extension,
                duration_seconds=math.ceil(result.duration_seconds) if result.duration_seconds is not None else None,
            )
            staged_audio.append(staged)
            audio_files.append(staged.audio_file)
            audio_file_durations_seconds.append(chunk_duration_seconds)
            elapsed_ms += chunk_duration_ms
            duration_seconds = elapsed_ms / 1000 if elapsed_ms > 0 else None
    except (ApplicationError, HTTPException):
        await discard_staged_audio_files(staged_audio)
        raise
    except Exception as exc:
        await discard_staged_audio_files(staged_audio)
        raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc

    if not audio_files:
        raise HTTPException(status_code=502, detail="TTS 未返回可用音频")
    return BookAudioSynthesis(
        audio_files=audio_files,
        audio_file_durations_seconds=audio_file_durations_seconds,
        staged_audio=staged_audio,
        provider=provider,
        model=model,
        mime_type=mime_type or audio_files[0].mime_type,
        source_text_hash=source_text_hash(text),
        duration_seconds=duration_seconds,
        alignment=alignment,
    )


def _chunk_duration_ms(alignment: list[TTSAlignmentSegment], duration_seconds: float | None) -> int:
    if alignment:
        return max(segment.end_ms for segment in alignment)
    if duration_seconds is not None and math.isfinite(duration_seconds) and duration_seconds > 0:
        return int(math.ceil(duration_seconds * 1000))
    return 0
