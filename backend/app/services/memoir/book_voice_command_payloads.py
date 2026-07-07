from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.memoir.book_voice_command_rules import (
    SUPPORTED_INTENTS,
    _clean_reply_text,
    _normalized_direction,
    _normalized_relative_position,
    _normalized_target_position,
    _optional_int,
    _optional_text,
)


@dataclass(frozen=True)
class ParsedCommandPayload:
    intent: str
    chapter_ref: str | None = None
    target_chapter_ref: str | None = None
    source_ref: str | None = None
    new_title: str | None = None
    direction: str | None = None
    target_position: str | None = None
    target_index: int | None = None
    relative_chapter_ref: str | None = None
    relative_position: str | None = None
    summary: str | None = None
    reply_text: str | None = None
    used_ai: bool = False
    provider: str | None = None
    model: str | None = None


def parsed_payload_from_json(payload: Any) -> ParsedCommandPayload:
    if not isinstance(payload, dict):
        return ParsedCommandPayload(intent="unknown")
    intent = str(payload.get("intent") or "").strip()
    if intent not in SUPPORTED_INTENTS:
        intent = "unknown"
    direction = _normalized_direction(payload.get("direction"))
    return ParsedCommandPayload(
        intent=intent,
        chapter_ref=_optional_text(payload.get("chapter_ref")),
        target_chapter_ref=_optional_text(payload.get("target_chapter_ref")),
        source_ref=_optional_text(payload.get("source_ref")),
        new_title=_optional_text(payload.get("new_title")),
        direction=direction,
        target_position=_normalized_target_position(payload.get("target_position")),
        target_index=_optional_int(payload.get("target_index")),
        relative_chapter_ref=_optional_text(payload.get("relative_chapter_ref")),
        relative_position=_normalized_relative_position(payload.get("relative_position")),
        summary=_optional_text(payload.get("summary")),
        reply_text=_clean_reply_text(payload.get("reply_text")),
    )
