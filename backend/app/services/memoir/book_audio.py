from __future__ import annotations

from dataclasses import dataclass
import hashlib

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import AudioFile
from app.schemas.memoir_book import MemoirBookAudioResponse
from app.services.memoir.audio_files import StagedAudioFile, discard_staged_audio, stage_audio_bytes
from app.services.memoir.voice_tts import TTSClient, TTS_UNAVAILABLE_MESSAGE, get_tts_client


MAX_TTS_CHARS_PER_CHUNK = 900


@dataclass(frozen=True)
class BookAudioSynthesis:
    audio_files: list[AudioFile]
    staged_audio: list[StagedAudioFile]
    provider: str
    model: str
    mime_type: str
    source_text_hash: str


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_tts_text(text: str, *, max_chars: int = MAX_TTS_CHARS_PER_CHUNK) -> list[str]:
    content = text.strip()
    if not content:
        raise HTTPException(status_code=400, detail="朗读内容为空")
    chunks: list[str] = []
    current = ""
    paragraphs = [item.strip() for item in content.splitlines() if item.strip()]
    for paragraph in paragraphs:
        pending = paragraph
        while pending:
            remaining = max_chars - len(current)
            if remaining <= 0:
                chunks.append(current.strip())
                current = ""
                remaining = max_chars
            if len(pending) <= remaining:
                current = f"{current}\n{pending}".strip() if current else pending
                pending = ""
                continue
            split_at = max(pending.rfind("。", 0, remaining), pending.rfind("，", 0, remaining))
            if split_at <= 0:
                split_at = remaining
            part = pending[: split_at + 1].strip()
            if current:
                chunks.append(current.strip())
                current = ""
            if part:
                chunks.append(part)
            pending = pending[split_at + 1 :].strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks


def cached_audio_response(
    metadata_json: dict,
    *,
    cache_key: str,
    text_hash: str,
    voice: str | None,
    force_regenerate: bool,
) -> MemoirBookAudioResponse | None:
    cache = metadata_json.get(cache_key)
    if force_regenerate or not isinstance(cache, dict):
        return None
    audio_file_ids = cache.get("audio_file_ids")
    if (
        cache.get("source_text_hash") == text_hash
        and (cache.get("voice") or None) == (voice or None)
        and isinstance(audio_file_ids, list)
        and all(isinstance(item, str) for item in audio_file_ids)
        and audio_file_ids
    ):
        return MemoirBookAudioResponse(
            audio_file_ids=audio_file_ids,
            provider=str(cache.get("provider") or "stored"),
            model=str(cache.get("model") or ""),
            mime_type=str(cache.get("mime_type") or "audio/mpeg"),
            chunk_count=int(cache.get("chunk_count") or len(audio_file_ids)),
            source_text_hash=text_hash,
        )
    return None


def audio_cache_payload(synthesis: BookAudioSynthesis, *, voice: str | None) -> dict[str, object]:
    return {
        "audio_file_ids": [audio_file.id for audio_file in synthesis.audio_files],
        "provider": synthesis.provider,
        "model": synthesis.model,
        "mime_type": synthesis.mime_type,
        "chunk_count": len(synthesis.audio_files),
        "source_text_hash": synthesis.source_text_hash,
        "voice": voice,
    }


def audio_response(synthesis: BookAudioSynthesis) -> MemoirBookAudioResponse:
    return MemoirBookAudioResponse(
        audio_file_ids=[audio_file.id for audio_file in synthesis.audio_files],
        provider=synthesis.provider,
        model=synthesis.model,
        mime_type=synthesis.mime_type,
        chunk_count=len(synthesis.audio_files),
        source_text_hash=synthesis.source_text_hash,
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
    tts_client: TTSClient | None = None,
) -> BookAudioSynthesis:
    chunks = split_tts_text(text)
    client = tts_client or get_tts_client()
    staged_audio: list[StagedAudioFile] = []
    audio_files: list[AudioFile] = []
    provider = client.provider_name
    model = client.model_name
    mime_type = ""
    try:
        for chunk in chunks:
            result = await client.synthesize(chunk, voice=voice)
            mime_type = result.mime_type
            provider = result.provider
            model = result.model
            staged = await stage_audio_bytes(
                db,
                user_id,
                result.audio,
                mime_type=result.mime_type,
                extension=result.extension,
                duration_seconds=None,
            )
            staged_audio.append(staged)
            audio_files.append(staged.audio_file)
    except HTTPException:
        await discard_staged_audio_files(staged_audio)
        raise
    except Exception as exc:
        await discard_staged_audio_files(staged_audio)
        raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc

    if not audio_files:
        raise HTTPException(status_code=502, detail="TTS 未返回可用音频")
    return BookAudioSynthesis(
        audio_files=audio_files,
        staged_audio=staged_audio,
        provider=provider,
        model=model,
        mime_type=mime_type or audio_files[0].mime_type,
        source_text_hash=source_text_hash(text),
    )
