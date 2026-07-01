from __future__ import annotations

from datetime import datetime

from app.models.memoir import MemoirVoiceUtterance
from app.schemas.memoir_book import MemoirBookSourceRouteResponse


MEMOIR_SOURCE_MATERIAL_TYPE = "memoir_content"
IGNORED_SOURCE_ROUTE_ACTION = "ignore"
IGNORED_SOURCE_MATERIAL_TYPES = {"ai_interaction", "meta_instruction", "noise", "too_short"}


def source_route_material_type(utterance: MemoirVoiceUtterance) -> str:
    return str(getattr(utterance, "source_material_type", "") or "").strip()


def source_route_action(utterance: MemoirVoiceUtterance) -> str:
    return str(getattr(utterance, "source_route_action", "") or "").strip()


def is_ignored_non_memoir_source(utterance: MemoirVoiceUtterance) -> bool:
    material_type = source_route_material_type(utterance)
    action = source_route_action(utterance)
    return action == IGNORED_SOURCE_ROUTE_ACTION and material_type in IGNORED_SOURCE_MATERIAL_TYPES


def is_countable_source_utterance(utterance: MemoirVoiceUtterance, text: str | None = None) -> bool:
    if text is None:
        text = (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()
    content = str(text or "").strip()
    return bool(content) and not is_ignored_non_memoir_source(utterance)


def apply_source_route_disposition(
    utterance: MemoirVoiceUtterance,
    route: MemoirBookSourceRouteResponse,
) -> None:
    utterance.source_material_type = route.material_type
    utterance.source_route_action = route.action
    utterance.source_route_confidence = route.confidence
    utterance.source_route_reason = route.reason
    utterance.source_route_updated_at = datetime.utcnow()


def clear_source_route_disposition(utterance: MemoirVoiceUtterance) -> None:
    utterance.source_material_type = ""
    utterance.source_route_action = ""
    utterance.source_route_confidence = None
    utterance.source_route_reason = ""
    utterance.source_route_updated_at = None
