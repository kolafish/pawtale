from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiofiles

from app.models.memoir import MemoirVoiceUtterance
from app.models.projects import Project
from app.services.asr import ASRAudioFormat, StepFunASRService
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.audio_files import audio_file_path
from app.services.asr.factory import create_volc_streaming_asr_client
from app.services.asr.billing import record_volc_streaming_asr_usage
from app.services.ai_costs import estimate_audio_cost
from app.services.ai_credits import assert_current_ai_credit_available
from app.services.ai_usage_records import record_ai_usage
from app.services.memoir.voice_transcript_recovery import (
    ASR_RECOVERY_PCM_BYTES_PER_SAMPLE,
    ASR_RECOVERY_PCM_SAMPLE_RATE,
    ASR_RECOVERY_PCM_SEGMENT_BYTES,
    transcribe_audio_path_for_recovery,
)


VOLC_OFFLINE_ASR_PROVIDER = "volc_streaming_asr_reprocess"
STEPFUN_OFFLINE_ASR_PROVIDER = "stepfun_asr_reprocess"
DEFAULT_OFFLINE_ASR_PROVIDERS = ("volc", "stepfun")
SUPPORTED_OFFLINE_ASR_PROVIDERS = frozenset(DEFAULT_OFFLINE_ASR_PROVIDERS)
MEMOIR_DIALECT_GLOSSARY_METADATA_KEY = "memoir_dialect_glossary_entries"


@dataclass(frozen=True)
class OfflineASRResult:
    provider: str
    text: str
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class OfflineASRGlossaryEntry:
    source: str
    target: str


@dataclass(frozen=True)
class OfflineASRRequest:
    utterance: MemoirVoiceUtterance
    providers: tuple[str, ...]
    hotwords: tuple[str, ...] = ()


ASRRunner = Callable[[OfflineASRRequest], Awaitable[list[OfflineASRResult]]]


def normalize_offline_asr_providers(value: Sequence[str] | str) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else list(value)
    providers = tuple(dict.fromkeys(item.strip().lower() for item in raw if item.strip()))
    unsupported = sorted(set(providers) - SUPPORTED_OFFLINE_ASR_PROVIDERS)
    if unsupported:
        raise ValueError(f"unsupported providers: {', '.join(unsupported)}")
    return providers or DEFAULT_OFFLINE_ASR_PROVIDERS


def offline_asr_provider_name(provider: str) -> str:
    if provider == "volc":
        return VOLC_OFFLINE_ASR_PROVIDER
    if provider == "stepfun":
        return STEPFUN_OFFLINE_ASR_PROVIDER
    raise ValueError(f"unsupported provider: {provider}")


def memoir_offline_asr_hotwords(project: Project) -> tuple[str, ...]:
    profile = _memoir_profile(project)
    title = project.title.strip()
    values = [
        str(profile.get("narrator_name") or "").strip(),
        str(profile.get("narrator_origin") or "").strip(),
        _title_subject(title),
        title if title != "我的回忆录" else "",
    ]
    for entry in memoir_offline_asr_glossary(project):
        values.extend((entry.source, entry.target))
    return tuple(dict.fromkeys(value for value in values if value))


def memoir_narrator_origin(project: Project) -> str:
    return str(_memoir_profile(project).get("narrator_origin") or "").strip()


def memoir_offline_asr_glossary(project: Project) -> tuple[OfflineASRGlossaryEntry, ...]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    raw_entries = metadata.get(MEMOIR_DIALECT_GLOSSARY_METADATA_KEY)
    if not isinstance(raw_entries, list):
        return ()
    entries: list[OfflineASRGlossaryEntry] = []
    seen: set[tuple[str, str]] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        source = str(raw_entry.get("source") or "").strip()
        target = str(raw_entry.get("target") or "").strip()
        key = (source, target)
        if not source or not target or key in seen:
            continue
        seen.add(key)
        entries.append(OfflineASRGlossaryEntry(source=source, target=target))
    return tuple(entries)


def _memoir_profile(project: Project) -> dict[str, object]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    profile = metadata.get("memoir_profile")
    return profile if isinstance(profile, dict) else {}


def _title_subject(title: str) -> str:
    if title == "我的回忆录":
        return ""
    for suffix in ("的回忆录", "的往事", "回忆录"):
        if title.endswith(suffix):
            return title.removesuffix(suffix).strip()
    return ""


async def _transcribe_with_volc(utterance: MemoirVoiceUtterance, path: Path) -> OfflineASRResult:
    await assert_current_ai_credit_available()
    duration_seconds = _stepfun_audio_duration_seconds(utterance, audio_bytes=path.stat().st_size)
    try:
        event = await transcribe_audio_path_for_recovery(
            path,
            asr_client_factory=lambda: create_volc_streaming_asr_client(
                utterance.audio_file.user_id,
                utterance.voice_session_id,
            ),
        )
        await record_volc_streaming_asr_usage(
            audio_duration_seconds=duration_seconds or 0,
            status="succeeded",
            provider=VOLC_OFFLINE_ASR_PROVIDER,
        )
        return OfflineASRResult(
            provider=VOLC_OFFLINE_ASR_PROVIDER,
            text=normalize_memoir_asr_text((event.text or "").strip()),
            raw={
                "provider": "volc_streaming_asr",
                "start_ms": event.start_ms,
                "end_ms": event.end_ms,
                "provider_payload": event.raw,
                "audio_duration_seconds": duration_seconds,
            },
        )
    except Exception as exc:
        await record_volc_streaming_asr_usage(
            audio_duration_seconds=duration_seconds or 0,
            status="failed",
            provider=VOLC_OFFLINE_ASR_PROVIDER,
            error_type=type(exc).__name__,
        )
        raise


def _stepfun_audio_format(utterance: MemoirVoiceUtterance) -> ASRAudioFormat:
    audio_file = utterance.audio_file
    mime_type = (audio_file.mime_type if audio_file else "").lower()
    if mime_type in {"audio/pcm", "audio/x-pcm"}:
        return ASRAudioFormat(
            type="pcm",
            codec="pcm_s16le",
            rate=ASR_RECOVERY_PCM_SAMPLE_RATE,
            bits=ASR_RECOVERY_PCM_BYTES_PER_SAMPLE * 8,
            channel=1,
        )
    if mime_type in {"audio/mpeg", "audio/mp3"}:
        return ASRAudioFormat(type="mp3")
    if mime_type in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return ASRAudioFormat(type="wav")
    if mime_type == "audio/ogg":
        return ASRAudioFormat(type="ogg")
    raise ValueError(f"unsupported StepFun ASR audio mime type: {mime_type or 'unknown'}")


def _stepfun_audio_duration_seconds(
    utterance: MemoirVoiceUtterance,
    *,
    audio_bytes: int | None = None,
) -> float | None:
    audio_file = utterance.audio_file
    stored_duration = audio_file.duration_seconds if audio_file else None
    if stored_duration and stored_duration > 0:
        return float(stored_duration)
    if audio_bytes and audio_file and audio_file.mime_type.lower() in {"audio/pcm", "audio/x-pcm"}:
        bytes_per_second = ASR_RECOVERY_PCM_SAMPLE_RATE * ASR_RECOVERY_PCM_BYTES_PER_SAMPLE
        return audio_bytes / bytes_per_second
    return None


async def _read_audio_segment(path: Path, *, start_byte: int, byte_count: int) -> bytes:
    async with aiofiles.open(path, "rb") as file:
        await file.seek(start_byte)
        return await file.read(byte_count)


async def _transcribe_with_stepfun(
    utterance: MemoirVoiceUtterance,
    path: Path,
    *,
    hotwords: Sequence[str],
) -> OfflineASRResult:
    service = StepFunASRService()
    size_bytes = path.stat().st_size
    duration_seconds = _stepfun_audio_duration_seconds(utterance, audio_bytes=size_bytes)
    try:
        await assert_current_ai_credit_available()
        audio_format = _stepfun_audio_format(utterance)
        if audio_format.type != "pcm" or size_bytes <= ASR_RECOVERY_PCM_SEGMENT_BYTES:
            audio_bytes = await _read_audio_segment(path, start_byte=0, byte_count=size_bytes)
            result = await service.transcribe(audio_bytes, audio_format, hotwords=list(hotwords))
            stepfun_cost = estimate_audio_cost(
                provider="stepfun",
                model=service.model,
                duration_seconds=duration_seconds,
            )
            await record_ai_usage(
                provider="stepfun",
                model=service.model,
                status="succeeded",
                cost=stepfun_cost,
                audio_duration_seconds=duration_seconds,
            )
            return OfflineASRResult(
                provider=STEPFUN_OFFLINE_ASR_PROVIDER,
                text=normalize_memoir_asr_text(result.text.strip()),
                raw={
                    "provider": "stepfun",
                    "model": service.model,
                    "usage": result.usage,
                    "segment_count": 1,
                    "audio_duration_seconds": duration_seconds,
                    "estimated_cost": stepfun_cost.as_dict(),
                },
            )

        texts: list[str] = []
        usages: list[dict[str, Any] | None] = []
        start_byte = 0
        segment_count = 0
        while start_byte < size_bytes:
            byte_count = min(ASR_RECOVERY_PCM_SEGMENT_BYTES, size_bytes - start_byte)
            if byte_count % ASR_RECOVERY_PCM_BYTES_PER_SAMPLE:
                byte_count -= byte_count % ASR_RECOVERY_PCM_BYTES_PER_SAMPLE
            if byte_count <= 0:
                break
            segment_count += 1
            audio_bytes = await _read_audio_segment(path, start_byte=start_byte, byte_count=byte_count)
            result = await service.transcribe(audio_bytes, audio_format, hotwords=list(hotwords))
            text = normalize_memoir_asr_text(result.text.strip())
            if text:
                texts.append(text)
            usages.append(result.usage)
            start_byte += byte_count

        stepfun_cost = estimate_audio_cost(
            provider="stepfun",
            model=service.model,
            duration_seconds=duration_seconds,
        )
        await record_ai_usage(
            provider="stepfun",
            model=service.model,
            status="succeeded",
            cost=stepfun_cost,
            audio_duration_seconds=duration_seconds,
        )
        return OfflineASRResult(
            provider=STEPFUN_OFFLINE_ASR_PROVIDER,
            text="\n".join(texts).strip(),
            raw={
                "provider": "stepfun",
                "model": service.model,
                "usage_by_segment": usages,
                "segment_count": segment_count,
                "audio_duration_seconds": duration_seconds,
                "estimated_cost": stepfun_cost.as_dict(),
            },
        )
    except Exception as exc:
        await record_ai_usage(
            provider="stepfun",
            model=service.model,
            status="failed",
            cost=estimate_audio_cost(
                provider="stepfun",
                model=service.model,
                duration_seconds=duration_seconds,
            ),
            audio_duration_seconds=duration_seconds,
            error_type=type(exc).__name__,
        )
        raise


async def run_offline_asr(request: OfflineASRRequest) -> list[OfflineASRResult]:
    path = audio_file_path(request.utterance.audio_file)
    if not path.exists():
        raise FileNotFoundError(f"audio file not found: {path}")
    results: list[OfflineASRResult] = []
    for provider in normalize_offline_asr_providers(request.providers):
        if provider == "volc":
            results.append(await _transcribe_with_volc(request.utterance, path))
        elif provider == "stepfun":
            results.append(
                await _transcribe_with_stepfun(
                    request.utterance,
                    path,
                    hotwords=request.hotwords,
                )
            )
    return [result for result in results if result.text.strip()]
