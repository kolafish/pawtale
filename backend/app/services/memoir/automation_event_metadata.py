from __future__ import annotations

from typing import Protocol

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
)


class AutomationEventMetadataCarrier(Protocol):
    event_type: str
    status: str
    chapter_id: str | None
    utterance_ids_json: object
    metadata_json: dict | None

ACKNOWLEDGED_AT_KEY = "acknowledged_at"
ACKNOWLEDGED_BY_USER_KEY = "acknowledged_by_user"
RESOLUTION_KEY = "resolution"
RESOLUTION_REASON_KEY = "resolution_reason"
SOURCE_DELETED_REASON = "source_deleted"
SOURCE_ASSIGNED_REASON = "source_assigned"
DELETED_UTTERANCE_IDS_KEY = "deleted_utterance_ids"
SOURCE_DELETED_AT_KEY = "source_deleted_at"
RESOLVED_AT_KEY = "resolved_at"
RESOLVED_CHAPTER_ID_KEY = "resolved_chapter_id"
RESOLVED_CHAPTER_TITLE_KEY = "resolved_chapter_title"
RESOLVED_UTTERANCE_IDS_KEY = "resolved_utterance_ids"
VOICE_SELECTED_AT_KEY = "voice_selected_at"
VOICE_SELECTED_SESSION_ID_KEY = "voice_selected_session_id"
VOICE_SELECTED_MESSAGE_ID_KEY = "voice_selected_message_id"
VOICE_PLAYED_AT_KEY = "voice_played_at"
VOICE_PLAYED_SESSION_ID_KEY = "voice_played_session_id"
VOICE_PLAYED_MESSAGE_ID_KEY = "voice_played_message_id"
EVENT_SIGNATURE_KEY = "event_signature"
ROUTE_KEY = "route"
ROUTES_KEY = "routes"
PENDING_SOURCE_UTTERANCE_IDS_KEY = "pending_source_utterance_ids"
UTTERANCE_ID_KEY = "utterance_id"
AUDIT_SNAPSHOT_KEY = "audit_snapshot"
AUDIT_SNAPSHOT_ID_KEY = "snapshot_id"


def _clean_utterance_ids(utterance_ids: list[str] | None) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in (utterance_ids or []) if item.strip()))


def _clean_unknown_utterance_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return _clean_utterance_ids([item for item in value if isinstance(item, str)])


def _metadata_utterance_ids(metadata: dict) -> list[str]:
    ids: list[str] = []
    value = metadata.get(UTTERANCE_ID_KEY)
    if isinstance(value, str):
        ids.append(value)

    route = metadata.get(ROUTE_KEY)
    if isinstance(route, dict):
        route_value = route.get(UTTERANCE_ID_KEY)
        if isinstance(route_value, str):
            ids.append(route_value)

    routes = metadata.get(ROUTES_KEY)
    if isinstance(routes, list):
        for route_item in routes:
            if not isinstance(route_item, dict):
                continue
            route_value = route_item.get(UTTERANCE_ID_KEY)
            if isinstance(route_value, str):
                ids.append(route_value)

    ids.extend(_clean_unknown_utterance_ids(metadata.get(PENDING_SOURCE_UTTERANCE_IDS_KEY)))
    return _clean_utterance_ids(ids)


def _event_utterance_ids(event: AutomationEventMetadataCarrier) -> list[str]:
    return _clean_utterance_ids(
        _clean_unknown_utterance_ids(event.utterance_ids_json)
        + _metadata_utterance_ids(event.metadata_json or {})
    )


def _without_deleted_ids(value: object, deleted_ids: set[str]) -> list[str]:
    return [item for item in _clean_unknown_utterance_ids(value) if item not in deleted_ids]


def _metadata_without_deleted_utterances(metadata: dict, deleted_ids: set[str]) -> dict:
    cleaned = dict(metadata)

    value = cleaned.get(UTTERANCE_ID_KEY)
    if isinstance(value, str) and value.strip() in deleted_ids:
        cleaned.pop(UTTERANCE_ID_KEY, None)

    route = cleaned.get(ROUTE_KEY)
    if isinstance(route, dict):
        route_copy = dict(route)
        route_value = route_copy.get(UTTERANCE_ID_KEY)
        if isinstance(route_value, str) and route_value.strip() in deleted_ids:
            route_copy.pop(UTTERANCE_ID_KEY, None)
        cleaned[ROUTE_KEY] = route_copy

    routes = cleaned.get(ROUTES_KEY)
    if isinstance(routes, list):
        kept_routes: list[object] = []
        for route_item in routes:
            if not isinstance(route_item, dict):
                kept_routes.append(route_item)
                continue
            route_value = route_item.get(UTTERANCE_ID_KEY)
            if isinstance(route_value, str) and route_value.strip() in deleted_ids:
                continue
            kept_routes.append(dict(route_item))
        cleaned[ROUTES_KEY] = kept_routes

    if PENDING_SOURCE_UTTERANCE_IDS_KEY in cleaned:
        cleaned[PENDING_SOURCE_UTTERANCE_IDS_KEY] = _without_deleted_ids(
            cleaned.get(PENDING_SOURCE_UTTERANCE_IDS_KEY),
            deleted_ids,
        )
    return cleaned


def _event_is_visible(event: AutomationEventMetadataCarrier) -> bool:
    metadata = event.metadata_json or {}
    if metadata.get(ACKNOWLEDGED_AT_KEY):
        return False
    if metadata.get(RESOLUTION_KEY) in {"confirmed", "ignored"}:
        return False
    return True


def _event_is_voice_unannounced(event: AutomationEventMetadataCarrier) -> bool:
    metadata = event.metadata_json or {}
    return _event_is_visible(event) and not metadata.get(VOICE_PLAYED_AT_KEY)


def _event_signature(metadata: dict) -> str:
    value = metadata.get(EVENT_SIGNATURE_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _metadata_source(metadata: dict) -> str:
    value = metadata.get("source")
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _metadata_int(metadata: dict, key: str) -> int:
    value = metadata.get(key)
    return value if isinstance(value, int) else 0


def _metadata_string(metadata: dict, key: str) -> str:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _legacy_audit_signature(metadata: dict) -> str:
    if _metadata_source(metadata) != "book_quality_audit":
        return ""
    overall_status = str(metadata.get("overall_status") or "")
    pending_count = _metadata_int(metadata, "pending_utterance_count")
    raw_items = metadata.get("chapter_items")
    chapter_items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
    chapter_parts = [
        ":".join(
            [
                str(item.get("chapter_id") or ""),
                str(item.get("status") or ""),
                str(item.get("attention") or ""),
                str(item.get("source_count") or 0),
                str(item.get("pending_source_count") or 0),
                str(item.get("draft_word_count") or 0),
            ]
        )
        for item in sorted(chapter_items, key=lambda value: (value.get("sort_order") or 0, value.get("chapter_id") or ""))
    ]
    return "|".join(["book_quality_audit", overall_status, f"pending={pending_count}", *chapter_parts])


def _event_semantic_signature(event: AutomationEventMetadataCarrier) -> str:
    metadata = event.metadata_json or {}
    signature = _event_signature(metadata)
    if signature:
        return signature

    event_type = event.event_type
    source = _metadata_source(metadata)
    if event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value:
        return _legacy_audit_signature(metadata)
    if event_type == MemoirAutomationEventType.CHAPTER_AUTO_REVISED.value and source == "book_auto_revision":
        pending_ids = _clean_unknown_utterance_ids(metadata.get(PENDING_SOURCE_UTTERANCE_IDS_KEY))
        source_ids = pending_ids or _clean_unknown_utterance_ids(event.utterance_ids_json)
        return f"book_auto_revision:{event.chapter_id or ''}:{','.join(sorted(source_ids))}" if source_ids else ""
    if event_type == MemoirAutomationEventType.SOURCE_ASSIGNED.value and source == "auto_assignment":
        source_ids = _clean_unknown_utterance_ids(event.utterance_ids_json)
        return f"source_assigned:{event.chapter_id or ''}:{','.join(sorted(source_ids))}" if source_ids else ""
    return ""


def _preserved_metadata(existing: dict) -> dict:
    return {
        key: existing[key]
        for key in (
            ACKNOWLEDGED_AT_KEY,
            ACKNOWLEDGED_BY_USER_KEY,
            VOICE_SELECTED_AT_KEY,
            VOICE_SELECTED_SESSION_ID_KEY,
            VOICE_SELECTED_MESSAGE_ID_KEY,
            VOICE_PLAYED_AT_KEY,
            VOICE_PLAYED_SESSION_ID_KEY,
            VOICE_PLAYED_MESSAGE_ID_KEY,
            RESOLUTION_KEY,
            RESOLUTION_REASON_KEY,
            DELETED_UTTERANCE_IDS_KEY,
            SOURCE_DELETED_AT_KEY,
            RESOLVED_AT_KEY,
            RESOLVED_CHAPTER_ID_KEY,
            RESOLVED_CHAPTER_TITLE_KEY,
            RESOLVED_UTTERANCE_IDS_KEY,
        )
        if key in existing
    }
