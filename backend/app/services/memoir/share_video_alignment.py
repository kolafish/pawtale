from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.memoir.types import (
    MemoirEditingJobType,
    MemoirImageAssetSourceType,
    MemoirImageAssetStatus,
)
from app.models.memoir import (
    MemoirEditingJob,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
    MemoirTranscriptAlignment,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_voice import (
    MemoirTranscriptShareVideoContextResponse,
    MemoirTranscriptShareVideoSegmentResponse,
)
from app.services.memoir import voice_transcripts
from app.services.memoir.share_video_backgrounds import (
    DEFAULT_SHARE_VIDEO_BACKGROUND_MODE,
    DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE,
    share_video_background_mode_options,
    share_video_background_style_options,
)
from app.services.memoir.share_video_custom_backgrounds import (
    DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE,
    share_video_background_source_options,
)
from app.services.memoir.transcript_projection import (
    current_utterance_text_and_source,
)


ALIGNMENT_STRATEGY_PROVIDER_WORDS = "provider_words"
ALIGNMENT_STRATEGY_PROVIDER_UTTERANCES = "provider_utterances"
ALIGNMENT_STRATEGY_LINEAR = "linear_estimate"
MIN_SELECT_MS = 3000
MAX_SELECT_MS = 90000
_PCM_BYTES_PER_SECOND = 16000 * 2
_ALIGN_IGNORED_RE = re.compile(r"[\s，。！？；、,.!?;:：…“”\"'（）()《》【】\[\]{}-]+")
_SENTENCE_ENDINGS = set("。！？!?；;\n")
_SOFT_ENDINGS = set("，、, ")


@dataclass(frozen=True)
class TimedChar:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class AlignmentBuildResult:
    text_hash: str
    text_source: str
    transcript_text: str
    duration_ms: int
    strategy: str
    confidence: float | None
    char_timings: list[dict[str, int]]
    segments: list[dict[str, object]]


def _stable_json_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _alignment_hash(utterance: MemoirVoiceUtterance, transcript_text: str, text_source: str) -> str:
    audio_file = utterance.audio_file
    payload = {
        "audio_file_id": utterance.audio_file_id,
        "audio_size_bytes": audio_file.size_bytes if audio_file else None,
        "audio_duration_seconds": audio_file.duration_seconds if audio_file else None,
        "end_ms": utterance.end_ms,
        "provider_payload_hash": _stable_json_hash(utterance.provider_payload_json or {}),
        "raw": utterance.transcript_raw or "",
        "source": text_source,
        "text": transcript_text,
    }
    return _stable_json_hash(payload)


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_ms(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _payload_result_dict(payload: dict[str, Any]) -> dict[str, Any]:
    payload_node = _as_dict(payload.get("payload"))
    nested_result = _as_dict(payload_node.get("result"))
    if nested_result:
        return nested_result
    result = _as_dict(payload.get("result"))
    if result:
        return result
    return payload_node or payload


def _payload_duration_ms(payload: dict[str, Any]) -> int | None:
    candidates = [
        _as_dict(_as_dict(payload.get("payload")).get("audio_info")).get("duration"),
        _as_dict(payload.get("audio_info")).get("duration"),
        _payload_result_dict(payload).get("duration"),
    ]
    for candidate in candidates:
        duration = _int_ms(candidate)
        if duration:
            return duration
    return None


def _char_parts(value: str) -> list[str]:
    return [char for char in value if char.strip()]


def _timed_chars_for_text(text: str, start_ms: int, end_ms: int) -> list[TimedChar]:
    chars = _char_parts(text)
    if not chars or end_ms <= start_ms:
        return []
    span = max(1, end_ms - start_ms)
    return [
        TimedChar(
            text=char,
            start_ms=start_ms + round(span * index / len(chars)),
            end_ms=start_ms + round(span * (index + 1) / len(chars)),
        )
        for index, char in enumerate(chars)
    ]


def _provider_timed_chars(utterance: MemoirVoiceUtterance) -> tuple[list[TimedChar], str]:
    payload = _as_dict(utterance.provider_payload_json)
    result = _payload_result_dict(payload)
    utterances = [item for item in _as_list(result.get("utterances")) if isinstance(item, dict)]
    timed_chars: list[TimedChar] = []
    used_words = False
    for item in utterances:
        utterance_start = _int_ms(item.get("start_time") or item.get("start_ms"))
        utterance_end = _int_ms(item.get("end_time") or item.get("end_ms"))
        words = [word for word in _as_list(item.get("words")) if isinstance(word, dict)]
        for word in words:
            text = str(word.get("text") or "")
            start_ms = _int_ms(word.get("start_time") or word.get("start_ms"))
            end_ms = _int_ms(word.get("end_time") or word.get("end_ms"))
            if text and start_ms is not None and end_ms is not None:
                timed_chars.extend(_timed_chars_for_text(text, start_ms, end_ms))
                used_words = True
        if not words and utterance_start is not None and utterance_end is not None:
            timed_chars.extend(_timed_chars_for_text(str(item.get("text") or ""), utterance_start, utterance_end))
    if timed_chars:
        strategy = ALIGNMENT_STRATEGY_PROVIDER_WORDS if used_words else ALIGNMENT_STRATEGY_PROVIDER_UTTERANCES
        return timed_chars, strategy
    return [], ALIGNMENT_STRATEGY_LINEAR


def _duration_ms(utterance: MemoirVoiceUtterance, timed_chars: list[TimedChar]) -> int:
    payload_duration = _payload_duration_ms(_as_dict(utterance.provider_payload_json))
    if payload_duration:
        return payload_duration
    if utterance.end_ms and utterance.end_ms > 0:
        return utterance.end_ms
    if timed_chars:
        return max(char.end_ms for char in timed_chars)
    audio_file = utterance.audio_file
    if audio_file and audio_file.duration_seconds:
        return max(1, int(audio_file.duration_seconds * 1000))
    if audio_file and audio_file.mime_type == "audio/pcm" and audio_file.size_bytes > 0:
        return max(1, round(audio_file.size_bytes / _PCM_BYTES_PER_SECOND * 1000))
    return 1


def _align_key(char: str) -> str:
    return "" if _ALIGN_IGNORED_RE.fullmatch(char) else char


def _linear_char_timings(text: str, duration_ms: int) -> list[dict[str, int]]:
    char_count = max(1, len(text))
    return [
        {
            "start_ms": round(duration_ms * index / char_count),
            "end_ms": round(duration_ms * (index + 1) / char_count),
        }
        for index, _char in enumerate(text)
    ]


def _interpolate_run(
    timings: list[dict[str, int] | None],
    indices: list[int],
    start_ms: int,
    end_ms: int,
) -> None:
    if not indices:
        return
    if end_ms <= start_ms:
        end_ms = start_ms + len(indices)
    span = end_ms - start_ms
    for offset, index in enumerate(indices):
        timings[index] = {
            "start_ms": start_ms + round(span * offset / len(indices)),
            "end_ms": start_ms + round(span * (offset + 1) / len(indices)),
        }


def _fill_missing_timings(
    timings: list[dict[str, int] | None],
    duration_ms: int,
) -> list[dict[str, int]]:
    known = [index for index, timing in enumerate(timings) if timing is not None]
    if not known:
        return _linear_char_timings(" " * len(timings), duration_ms)

    first = known[0]
    if first > 0:
        first_start = timings[first]["start_ms"] if timings[first] else 0
        _interpolate_run(timings, list(range(0, first)), 0, first_start)

    for left, right in zip(known, known[1:]):
        if right - left <= 1:
            continue
        left_end = timings[left]["end_ms"] if timings[left] else 0
        right_start = timings[right]["start_ms"] if timings[right] else left_end
        _interpolate_run(timings, list(range(left + 1, right)), left_end, right_start)

    last = known[-1]
    if last < len(timings) - 1:
        last_end = timings[last]["end_ms"] if timings[last] else 0
        _interpolate_run(timings, list(range(last + 1, len(timings))), last_end, duration_ms)

    return [
        timing if timing is not None else {"start_ms": 0, "end_ms": duration_ms}
        for timing in timings
    ]


def _align_char_timings(
    transcript_text: str,
    raw_timed_chars: list[TimedChar],
    duration_ms: int,
) -> tuple[list[dict[str, int]], float | None]:
    if not transcript_text:
        return [], None
    if not raw_timed_chars:
        return _linear_char_timings(transcript_text, duration_ms), 0.4

    raw_units = [(char, _align_key(char.text)) for char in raw_timed_chars if _align_key(char.text)]
    edited_units = [(index, _align_key(char)) for index, char in enumerate(transcript_text) if _align_key(char)]
    if not raw_units or not edited_units:
        return _linear_char_timings(transcript_text, duration_ms), 0.4

    raw_keys = [key for _char, key in raw_units]
    edited_keys = [key for _index, key in edited_units]
    matcher = SequenceMatcher(None, raw_keys, edited_keys, autojunk=False)
    timings: list[dict[str, int] | None] = [None] * len(transcript_text)
    matched = 0
    for tag, raw_start, raw_end, edited_start, edited_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(min(raw_end - raw_start, edited_end - edited_start)):
            timed_char = raw_units[raw_start + offset][0]
            edited_index = edited_units[edited_start + offset][0]
            timings[edited_index] = {
                "start_ms": timed_char.start_ms,
                "end_ms": timed_char.end_ms,
            }
            matched += 1

    confidence = max(0.4, min(0.95, matched / max(1, len(edited_units))))
    return _fill_missing_timings(timings, duration_ms), round(confidence, 3)


def _trim_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _split_long_range(text: str, start: int, end: int, *, max_chars: int = 42) -> list[tuple[int, int]]:
    if end - start <= max_chars:
        return [(start, end)]
    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        target = min(end, cursor + max_chars)
        split_at = None
        for index in range(target - 1, cursor, -1):
            if text[index] in _SOFT_ENDINGS:
                split_at = index + 1
                break
        if split_at is None or split_at <= cursor:
            split_at = target
        ranges.append((cursor, split_at))
        cursor = split_at
    return ranges


def _segment_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_ENDINGS:
            segment_start, segment_end = _trim_range(text, start, index + 1)
            if segment_start < segment_end:
                ranges.extend(_split_long_range(text, segment_start, segment_end))
            start = index + 1
    segment_start, segment_end = _trim_range(text, start, len(text))
    if segment_start < segment_end:
        ranges.extend(_split_long_range(text, segment_start, segment_end))
    return ranges


def _segments(
    text: str,
    char_timings: list[dict[str, int]],
    confidence: float | None,
) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    for start, end in _segment_ranges(text):
        if start >= len(char_timings) or end <= start:
            continue
        start_ms = int(char_timings[start]["start_ms"])
        end_ms = int(char_timings[min(end - 1, len(char_timings) - 1)]["end_ms"])
        segments.append(
            {
                "index": len(segments),
                "text": text[start:end].strip(),
                "start_char": start,
                "end_char": end,
                "start_ms": max(0, start_ms),
                "end_ms": max(start_ms + 1, end_ms),
                "confidence": confidence,
                "selectable": end_ms > start_ms,
            }
        )
    return segments


def _build_alignment(utterance: MemoirVoiceUtterance) -> AlignmentBuildResult:
    transcript_text, text_source = current_utterance_text_and_source(utterance)
    if not transcript_text:
        raise HTTPException(status_code=400, detail="素材还没有可分享的校正文")
    if not utterance.audio_file_id or not utterance.audio_file:
        raise HTTPException(status_code=400, detail="素材没有可分享的音频")

    timed_chars, strategy = _provider_timed_chars(utterance)
    duration_ms = _duration_ms(utterance, timed_chars)
    if strategy == ALIGNMENT_STRATEGY_LINEAR:
        raw_text = (utterance.transcript_raw or transcript_text).strip()
        timed_chars = _timed_chars_for_text(raw_text, 0, duration_ms)
    char_timings, confidence = _align_char_timings(transcript_text, timed_chars, duration_ms)
    segments = _segments(transcript_text, char_timings, confidence)
    return AlignmentBuildResult(
        text_hash=_alignment_hash(utterance, transcript_text, text_source),
        text_source=text_source,
        transcript_text=transcript_text,
        duration_ms=duration_ms,
        strategy=strategy,
        confidence=confidence,
        char_timings=char_timings,
        segments=segments,
    )


def build_share_video_alignment(utterance: MemoirVoiceUtterance) -> AlignmentBuildResult:
    return _build_alignment(utterance)


def share_video_char_range_ms(
    result: AlignmentBuildResult,
    start_char: int,
    end_char: int,
) -> tuple[int, int]:
    if start_char < 0 or end_char > len(result.char_timings) or end_char <= start_char:
        raise HTTPException(status_code=400, detail="选择的文字范围无效")
    start_ms = int(result.char_timings[start_char]["start_ms"])
    end_ms = int(result.char_timings[end_char - 1]["end_ms"])
    return max(0, start_ms), max(start_ms + 1, end_ms)


def _response(
    utterance: MemoirVoiceUtterance,
    result: AlignmentBuildResult,
) -> MemoirTranscriptShareVideoContextResponse:
    settings = get_settings()
    return MemoirTranscriptShareVideoContextResponse(
        utterance_id=utterance.id,
        audio_file_id=utterance.audio_file_id or "",
        transcript_source=result.text_source,
        transcript_text=result.transcript_text,
        text_hash=result.text_hash,
        duration_ms=result.duration_ms,
        alignment_strategy=result.strategy,
        alignment_confidence=result.confidence,
        min_select_ms=MIN_SELECT_MS,
        max_select_ms=MAX_SELECT_MS,
        ai_background_enabled=bool(settings.memoir_share_video_ai_background_enabled),
        default_background_source=DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE,
        default_background_style=DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE,
        default_background_mode=DEFAULT_SHARE_VIDEO_BACKGROUND_MODE,
        background_sources=share_video_background_source_options(),
        background_styles=share_video_background_style_options(),
        background_modes=share_video_background_mode_options(),
        segments=[
            MemoirTranscriptShareVideoSegmentResponse(**segment)
            for segment in result.segments
        ],
    )


async def get_share_video_context(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> MemoirTranscriptShareVideoContextResponse:
    utterance = await voice_transcripts._load_owned_utterance(  # noqa: SLF001
        db,
        utterance_id,
        user_id,
        allow_admin=allow_admin,
    )
    result = _build_alignment(utterance)
    response = _response(utterance, result)
    response.latest_job_id = await db.scalar(
        select(MemoirMaterialShareVideo.id)
        .where(
            MemoirMaterialShareVideo.utterance_id == utterance.id,
            MemoirMaterialShareVideo.text_hash == result.text_hash,
        )
        .order_by(
            MemoirMaterialShareVideo.created_at.desc(),
            MemoirMaterialShareVideo.id.desc(),
        )
        .limit(1)
    )
    latest_image_assets = (
        await db.scalars(
            select(MemoirImageAsset)
            .where(
                MemoirImageAsset.project_id == utterance.voice_session.project_id,
                MemoirImageAsset.source_utterance_id == utterance.id,
                MemoirImageAsset.source_type
                == MemoirImageAssetSourceType.UTTERANCE_ILLUSTRATION.value,
                MemoirImageAsset.status != MemoirImageAssetStatus.DELETED.value,
            )
            .order_by(
                MemoirImageAsset.created_at.desc(),
                MemoirImageAsset.id.desc(),
            )
            .limit(20)
        )
    ).all()
    for asset in latest_image_assets:
        metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
        if metadata.get("transcript_text_hash") != result.text_hash:
            continue
        generation_job_id = str(metadata.get("generation_job_id") or "").strip()
        if not generation_job_id:
            continue
        job_exists = await db.scalar(
            select(MemoirEditingJob.id).where(
                MemoirEditingJob.id == generation_job_id,
                MemoirEditingJob.project_id == utterance.voice_session.project_id,
                MemoirEditingJob.job_type
                == MemoirEditingJobType.GENERATE_MATERIAL_IMAGE.value,
            )
        )
        if job_exists:
            response.latest_image_job_id = generation_job_id
            break
    alignment = await db.scalar(
        select(MemoirTranscriptAlignment).where(MemoirTranscriptAlignment.utterance_id == utterance.id)
    )
    if alignment is None:
        alignment = MemoirTranscriptAlignment(utterance_id=utterance.id)
    if alignment.text_hash != result.text_hash:
        alignment.text_source = result.text_source
        alignment.text_hash = result.text_hash
        alignment.strategy = result.strategy
        alignment.confidence = result.confidence
        alignment.duration_ms = result.duration_ms
        alignment.alignment_json = {
            "char_timings": result.char_timings,
            "min_select_ms": MIN_SELECT_MS,
            "max_select_ms": MAX_SELECT_MS,
        }
        alignment.segments_json = result.segments
        db.add(alignment)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
    return response
