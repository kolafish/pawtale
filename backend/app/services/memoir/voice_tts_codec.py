from __future__ import annotations

from dataclasses import dataclass
import math

from fastapi import HTTPException

from app.services.memoir.voice_tts_types import (
    DEFAULT_STREAM_TTS_BITS_PER_SAMPLE,
    DEFAULT_STREAM_TTS_CHANNELS,
    DEFAULT_STREAM_TTS_SAMPLE_RATE,
    DEFAULT_TTS_SPEED_RATIO,
)


SUPPORTED_RESPONSE_FORMATS = {
    "mp3": ("audio/mpeg", ".mp3", "mp3"),
    "wav": ("audio/wav", ".wav", "wav"),
    "ogg": ("audio/ogg", ".ogg", "ogg_opus"),
    "ogg_opus": ("audio/ogg", ".ogg", "ogg_opus"),
    "pcm": ("audio/L16", ".pcm", "pcm"),
}
PCM_MIME_TYPES = {"audio/l16", "audio/pcm", "audio/x-pcm"}


@dataclass(frozen=True)
class VolcTTSResponseFormat:
    mime_type: str
    extension: str
    provider_value: str


def volc_response_format(value: str) -> VolcTTSResponseFormat:
    normalized = (value or "mp3").strip().lower()
    if normalized not in SUPPORTED_RESPONSE_FORMATS:
        raise HTTPException(status_code=500, detail=f"不支持的火山 TTS 音频格式: {normalized}")
    mime_type, extension, provider_value = SUPPORTED_RESPONSE_FORMATS[normalized]
    return VolcTTSResponseFormat(mime_type=mime_type, extension=extension, provider_value=provider_value)


def volc_effective_voice(requested_voice: str | None, default_voice: str) -> str:
    candidate = (requested_voice or "").strip()
    if looks_like_volc_v3_voice(candidate):
        return candidate
    return default_voice.strip()


def looks_like_volc_v3_voice(voice: str) -> bool:
    if not voice:
        return False
    return (
        voice.startswith("S_")
        or voice.endswith("_bigtts")
        or voice.endswith("_tob")
        or voice.startswith("ICL_")
        or voice.startswith("multi_")
    )


def effective_tts_speed_ratio(speed_ratio: object) -> float:
    if speed_ratio is None:
        return DEFAULT_TTS_SPEED_RATIO
    try:
        value = float(speed_ratio)
    except (TypeError, ValueError):
        return DEFAULT_TTS_SPEED_RATIO
    if not math.isfinite(value):
        return DEFAULT_TTS_SPEED_RATIO
    return max(0.5, min(2.0, round(value, 2)))


def effective_stream_sample_rate(sample_rate: object) -> int:
    if sample_rate is None:
        return DEFAULT_STREAM_TTS_SAMPLE_RATE
    try:
        value = int(sample_rate)
    except (TypeError, ValueError):
        return DEFAULT_STREAM_TTS_SAMPLE_RATE
    if value <= 0:
        return DEFAULT_STREAM_TTS_SAMPLE_RATE
    return value


def stream_audio_duration_seconds(audio_bytes: int, mime_type: str, sample_rate: int) -> float | None:
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized not in PCM_MIME_TYPES:
        return None
    bytes_per_second = sample_rate * DEFAULT_STREAM_TTS_CHANNELS * DEFAULT_STREAM_TTS_BITS_PER_SAMPLE / 8
    if bytes_per_second <= 0:
        return None
    return audio_bytes / bytes_per_second


def volc_speech_rate_from_speed_ratio(speed_ratio: float) -> int | None:
    effective_speed_ratio = effective_tts_speed_ratio(speed_ratio)
    if effective_speed_ratio == DEFAULT_TTS_SPEED_RATIO:
        return None
    return max(-50, min(100, int(round((effective_speed_ratio - 1.0) * 100))))
