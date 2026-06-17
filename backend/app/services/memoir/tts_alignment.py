from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol

import httpx
from fastapi import HTTPException

from app.config import Settings, get_settings
from app.services.provider_limits import provider_concurrency_limiter


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TTSAlignmentSegment:
    text: str
    start_ms: int
    end_ms: int
    text_start: int
    text_end: int
    confidence: float | None = None


@dataclass(frozen=True)
class TTSAlignmentResult:
    provider: str
    segments: list[TTSAlignmentSegment]
    duration_seconds: float | None = None
    loss: float | None = None


class TTSAlignmentClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    async def align(
        self,
        text: str,
        audio: bytes,
        *,
        mime_type: str,
        extension: str,
    ) -> TTSAlignmentResult:
        ...


def alignment_segments_to_json(segments: list[TTSAlignmentSegment]) -> list[dict[str, object]]:
    return [
        {
            "text": segment.text,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "text_start": segment.text_start,
            "text_end": segment.text_end,
            **({"confidence": segment.confidence} if segment.confidence is not None else {}),
        }
        for segment in segments
    ]


def alignment_segments_from_json(value: object) -> list[TTSAlignmentSegment]:
    if not isinstance(value, list):
        return []
    segments: list[TTSAlignmentSegment] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            text = str(item.get("text") or "")
            start_ms = int(item.get("start_ms"))
            end_ms = int(item.get("end_ms"))
            text_start = int(item.get("text_start"))
            text_end = int(item.get("text_end"))
        except (TypeError, ValueError):
            continue
        if not text or start_ms < 0 or end_ms <= start_ms or text_start < 0 or text_end <= text_start:
            continue
        confidence_value = item.get("confidence")
        try:
            confidence = float(confidence_value) if confidence_value is not None else None
        except (TypeError, ValueError):
            confidence = None
        segments.append(
            TTSAlignmentSegment(
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                text_start=text_start,
                text_end=text_end,
                confidence=confidence,
            )
        )
    return segments


def _float_seconds(value: object) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return seconds


def _match_character_offsets(text: str, aligned_characters: list[dict[str, object]]) -> list[TTSAlignmentSegment]:
    source_characters = list(text)
    source_index = 0
    segments: list[TTSAlignmentSegment] = []
    for item in aligned_characters:
        character = str(item.get("text") or "")
        if not character:
            continue
        start_seconds = _float_seconds(item.get("start"))
        end_seconds = _float_seconds(item.get("end"))
        if start_seconds is None or end_seconds is None or end_seconds <= start_seconds:
            continue

        character_length = max(1, len(list(character)))
        if source_index >= len(source_characters):
            break
        if "".join(source_characters[source_index : source_index + character_length]) != character:
            try:
                next_index = source_characters.index(character[0], source_index)
            except ValueError:
                next_index = source_index
            source_index = next_index

        text_start = source_index
        text_end = min(len(source_characters), source_index + character_length)
        source_index = text_end
        start_ms = int(round(start_seconds * 1000))
        end_ms = max(start_ms + 1, int(round(end_seconds * 1000)))
        segments.append(
            TTSAlignmentSegment(
                text=character,
                start_ms=start_ms,
                end_ms=end_ms,
                text_start=text_start,
                text_end=text_end,
            )
        )
    return segments


class ElevenLabsForcedAlignmentClient:
    provider_name = "elevenlabs_forced_alignment"

    def __init__(self, settings: Settings):
        self.api_key = settings.elevenlabs_api_key or settings.tts_alignment_api_key
        self.base_url = (settings.elevenlabs_base_url or settings.tts_alignment_base_url).strip().rstrip("/")
        self.timeout_seconds = settings.tts_alignment_timeout_seconds
        self.max_concurrent_requests = settings.tts_alignment_max_concurrent_requests

    def _endpoint(self) -> str:
        base_url = self.base_url or "https://api.elevenlabs.io/v1"
        if base_url.endswith("/forced-alignment"):
            return base_url
        return f"{base_url}/forced-alignment"

    async def align(
        self,
        text: str,
        audio: bytes,
        *,
        mime_type: str,
        extension: str,
    ) -> TTSAlignmentResult:
        api_key = self.api_key.strip()
        if not api_key:
            raise HTTPException(status_code=503, detail="ElevenLabs alignment API key 未配置")
        filename = f"tts-audio{extension if extension.startswith('.') else f'.{extension}'}"
        timeout = max(1.0, float(self.timeout_seconds))
        async def request() -> httpx.Response:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self._endpoint(),
                    data={"text": text},
                    files={"file": (filename, audio, mime_type)},
                    headers={"xi-api-key": api_key},
                )
                response.raise_for_status()
                return response

        try:
            response = await provider_concurrency_limiter.run(
                "tts_alignment",
                self.max_concurrent_requests,
                request,
            )
        except httpx.HTTPStatusError as exc:
            logger.exception("TTS alignment provider rejected request status=%s", exc.response.status_code)
            raise HTTPException(status_code=503, detail="TTS 对齐服务暂时不可用") from exc
        except httpx.HTTPError as exc:
            logger.exception("TTS alignment provider request failed")
            raise HTTPException(status_code=503, detail="TTS 对齐服务暂时不可用") from exc

        payload = response.json()
        raw_characters = payload.get("characters")
        characters = raw_characters if isinstance(raw_characters, list) else []
        segments = _match_character_offsets(text, [item for item in characters if isinstance(item, dict)])
        loss = _float_seconds(payload.get("loss"))
        duration_seconds = max((segment.end_ms for segment in segments), default=0) / 1000 if segments else None
        return TTSAlignmentResult(
            provider=self.provider_name,
            segments=segments,
            duration_seconds=duration_seconds,
            loss=loss,
        )


def get_tts_alignment_client(settings: Settings | None = None) -> TTSAlignmentClient | None:
    current_settings = settings or get_settings()
    provider = current_settings.tts_alignment_provider.strip().lower()
    if provider in {"", "disabled", "none"}:
        return None
    if provider in {"elevenlabs", "elevenlabs_forced_alignment"}:
        return ElevenLabsForcedAlignmentClient(current_settings)
    raise HTTPException(status_code=503, detail=f"不支持的 TTS_ALIGNMENT_PROVIDER: {provider}")


async def align_tts_audio(
    text: str,
    audio: bytes,
    *,
    mime_type: str,
    extension: str,
    settings: Settings | None = None,
) -> TTSAlignmentResult | None:
    client = get_tts_alignment_client(settings)
    if client is None:
        return None
    if not text.strip() or not audio:
        return None
    return await client.align(text.strip(), audio, mime_type=mime_type, extension=extension)
