from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TTSAlignmentSegment:
    text: str
    start_ms: int
    end_ms: int
    text_start: int
    text_end: int
    confidence: float | None = None


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


def alignment_segments_from_volc_words(text: str, words: list[object]) -> list[TTSAlignmentSegment]:
    cursor = 0
    segments: list[TTSAlignmentSegment] = []
    for raw_word in words:
        if not isinstance(raw_word, dict):
            continue
        word = str(raw_word.get("word") or "")
        start_seconds = _non_negative_float(raw_word.get("startTime"))
        end_seconds = _non_negative_float(raw_word.get("endTime"))
        if not word or start_seconds is None or end_seconds is None or end_seconds <= start_seconds:
            continue

        text_start = _find_text_offset(text, word, cursor)
        text_end = min(len(text), text_start + len(word))
        if text_end <= text_start:
            continue
        cursor = max(cursor, text_end)

        start_ms = int(round(start_seconds * 1000))
        end_ms = max(start_ms + 1, int(round(end_seconds * 1000)))
        confidence = _non_negative_float(raw_word.get("confidence"))
        segments.append(
            TTSAlignmentSegment(
                text=text[text_start:text_end],
                start_ms=start_ms,
                end_ms=end_ms,
                text_start=text_start,
                text_end=text_end,
                confidence=confidence,
            )
        )
    return segments


def _non_negative_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _find_text_offset(text: str, word: str, cursor: int) -> int:
    exact = text.find(word, cursor)
    if exact >= 0:
        return exact

    stripped = word.strip()
    if stripped and stripped != word:
        stripped_index = text.find(stripped, cursor)
        if stripped_index >= 0:
            return stripped_index

    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return min(cursor, len(text))
