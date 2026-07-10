from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.services.memoir.asr import StreamingASREvent


@dataclass(frozen=True)
class SpeakerSegment:
    segment_id: str
    speaker_id: str
    text: str
    stable_key: str = ""
    speaker_gender: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    confidence: float | None = None


def speaker_segments_from_asr_event(event: StreamingASREvent) -> list[SpeakerSegment]:
    payload = _provider_payload(event.raw)
    if payload is None:
        return []
    utterances = _provider_utterances(payload)
    segments: list[SpeakerSegment] = []
    for index, utterance in enumerate(utterances, start=1):
        text = str(utterance.get("text") or "").strip()
        speaker = _speaker_id(utterance)
        if not text or not speaker:
            continue
        start_ms = _optional_int(_first_present(utterance, "start_ms", "start_time"))
        end_ms = _optional_int(_first_present(utterance, "end_ms", "end_time"))
        segments.append(
            SpeakerSegment(
                segment_id=f"provider_{index}",
                speaker_id=speaker,
                text=text,
                stable_key=_segment_stable_key(
                    utterance,
                    speaker_id=speaker,
                    index=index,
                    start_ms=start_ms,
                    text=text,
                ),
                speaker_gender=_speaker_gender(utterance),
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=_optional_float(utterance.get("confidence")),
            )
        )
    if segments:
        return segments
    for index, base in enumerate(_provider_text_bases(payload), start=1):
        text = str(base.get("text") or "").strip()
        speaker = _speaker_id(base)
        if not text or not speaker:
            continue
        start_ms = _optional_int(_first_present(base, "start_ms", "start_time"))
        end_ms = _optional_int(_first_present(base, "end_ms", "end_time"))
        segments.append(
            SpeakerSegment(
                segment_id=f"provider_{index}",
                speaker_id=speaker,
                text=text,
                stable_key=_segment_stable_key(
                    base,
                    speaker_id=speaker,
                    index=index,
                    start_ms=start_ms,
                    text=text,
                ),
                speaker_gender=_speaker_gender(base),
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=_optional_float(base.get("confidence")),
            )
        )
    return segments


def _provider_payload(raw: dict[str, Any]) -> dict[str, Any] | None:
    payload = raw.get("payload") if isinstance(raw, dict) else None
    return payload if isinstance(payload, dict) else None


def _provider_utterances(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for base in _provider_text_bases(payload):
        utterances = base.get("utterances")
        if isinstance(utterances, list):
            return [item for item in utterances if isinstance(item, dict)]
    return []


def _provider_text_bases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    bases: list[dict[str, Any]] = []
    if isinstance(result, list):
        bases.extend(item for item in result if isinstance(item, dict))
    elif isinstance(result, dict):
        bases.append(result)
    bases.append(payload)
    return bases


SPEAKER_ID_KEYS = (
    "speaker_id",
    "speaker",
    "speaker_label",
    "spk_id",
    "spk",
    "speakerId",
    "speakerID",
    "speaker_no",
    "speakerNo",
)
SPEAKER_CONTAINER_KEYS = ("speaker_info", "speakerInfo", "additions", "speaker")
SEGMENT_ID_KEYS = (
    "utterance_id",
    "utteranceId",
    "utt_id",
    "uttId",
    "segment_id",
    "segmentId",
    "result_id",
    "resultId",
    "id",
)
SPEAKER_GENDER_KEYS = (
    "speaker_gender",
    "speakerGender",
    "gender",
    "sex",
    "voice_gender",
    "voiceGender",
)


def _segment_stable_key(
    payload: dict[str, Any],
    *,
    speaker_id: str,
    index: int,
    start_ms: int | None,
    text: str,
) -> str:
    explicit_key = _explicit_segment_key(payload)
    if explicit_key:
        return f"id:{speaker_id}:{explicit_key}"
    return fallback_segment_key(
        speaker_id=speaker_id,
        text=text,
        index=index,
        start_ms=start_ms,
    )


def _explicit_segment_key(payload: dict[str, Any]) -> str:
    for key in SEGMENT_ID_KEYS:
        value = payload.get(key)
        if value is None or isinstance(value, bool):
            continue
        text = str(value).strip()
        if text:
            return text.lower()
    return ""


def fallback_segment_key(
    *,
    speaker_id: str,
    text: str,
    index: int,
    start_ms: int | None,
) -> str:
    if start_ms is not None:
        return f"span:{speaker_id}:{start_ms}"
    return f"text:{speaker_id}:{index}:{_text_key(text)}"


def _text_key(text: str) -> str:
    return "".join(str(text).split()).lower()[:120]


@dataclass(frozen=True)
class _ComparableSpeechChar:
    char: str
    end: int


def _comparable_speech_chars(text: str) -> list[_ComparableSpeechChar]:
    return [
        _ComparableSpeechChar(char=char.lower(), end=index + len(char))
        for index, char in enumerate(text)
        if char.isalnum()
    ]


def _trim_speech_boundary(text: str) -> str:
    return text.strip().lstrip("，。？！、；：,.?!;:").strip()


def live_text_after_confirmed_prefix(text: str, confirmed_prefix: str) -> str:
    clean_text = text.strip()
    clean_prefix = confirmed_prefix.strip()
    if not clean_text or not clean_prefix:
        return clean_text
    text_chars = _comparable_speech_chars(clean_text)
    prefix_chars = _comparable_speech_chars(clean_prefix)
    if not text_chars or not prefix_chars:
        return clean_text
    text_key = "".join(char.char for char in text_chars)
    prefix_key = "".join(char.char for char in prefix_chars)
    if text_key.startswith(prefix_key):
        return _trim_speech_boundary(clean_text[text_chars[len(prefix_chars) - 1].end :])
    if prefix_key.startswith(text_key) or prefix_key.endswith(text_key):
        return ""
    max_overlap = min(len(text_key), len(prefix_key))
    for size in range(max_overlap, 0, -1):
        if prefix_key.endswith(text_key[:size]):
            return _trim_speech_boundary(clean_text[text_chars[size - 1].end :])
    return clean_text


def _speaker_id(utterance: dict[str, Any]) -> str:
    for key in SPEAKER_ID_KEYS:
        speaker = _speaker_id_from_value(utterance.get(key))
        if speaker:
            return speaker
    for key in SPEAKER_CONTAINER_KEYS:
        container = _as_dict(utterance.get(key))
        if container is None:
            continue
        speaker = _speaker_id_from_mapping(container)
        if speaker:
            return speaker
    return ""


def _speaker_gender(utterance: dict[str, Any]) -> str | None:
    for key in SPEAKER_GENDER_KEYS:
        gender = _speaker_gender_from_value(utterance.get(key))
        if gender:
            return gender
    for key in SPEAKER_CONTAINER_KEYS:
        container = _as_dict(utterance.get(key))
        if container is None:
            continue
        gender = _speaker_gender_from_mapping(container)
        if gender:
            return gender
    return None


def _speaker_gender_from_mapping(value: dict[str, Any]) -> str | None:
    for key in SPEAKER_GENDER_KEYS:
        gender = _speaker_gender_from_value(value.get(key))
        if gender:
            return gender
    return None


def _speaker_gender_from_value(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    nested = _as_dict(value)
    if nested is not None:
        return _speaker_gender_from_mapping(nested)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"male", "man", "m", "男", "男声", "男性"}:
        return "male"
    if text in {"female", "woman", "f", "女", "女声", "女性"}:
        return "female"
    return None


def _speaker_id_from_mapping(value: dict[str, Any]) -> str:
    for key in SPEAKER_ID_KEYS + ("id", "label"):
        speaker = _speaker_id_from_value(value.get(key))
        if speaker:
            return speaker
    return ""


def _speaker_id_from_value(value: Any) -> str:
    if value is None:
        return ""
    nested = _as_dict(value)
    if nested is not None:
        return _speaker_id_from_mapping(nested)
    if isinstance(value, (list, tuple)):
        for item in value:
            speaker = _speaker_id_from_value(item)
            if speaker:
                return speaker
        return ""
    if isinstance(value, bool):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return _normalize_speaker_id(text)


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw.startswith("{"):
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _normalize_speaker_id(value: Any) -> str:
    text = str(value).strip().lower()
    if not text.startswith("speaker_"):
        return f"speaker_{text}"
    return text


def _first_present(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if value.get(key) is not None:
            return value[key]
    return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
