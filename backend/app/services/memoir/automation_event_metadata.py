from __future__ import annotations

from typing import Protocol


class AutomationEventMetadataCarrier(Protocol):
    utterance_ids_json: object
    metadata_json: dict | None


ACKNOWLEDGED_AT_KEY = "acknowledged_at"
ACKNOWLEDGED_BY_USER_KEY = "acknowledged_by_user"
VOICE_SELECTED_AT_KEY = "voice_selected_at"
VOICE_SELECTED_SESSION_ID_KEY = "voice_selected_session_id"
VOICE_SELECTED_MESSAGE_ID_KEY = "voice_selected_message_id"
VOICE_PLAYED_AT_KEY = "voice_played_at"
VOICE_PLAYED_SESSION_ID_KEY = "voice_played_session_id"
VOICE_PLAYED_MESSAGE_ID_KEY = "voice_played_message_id"
EVENT_SIGNATURE_KEY = "event_signature"
EDITING_JOB_FAILURE_SOURCE = "editing_job_failure"


def _clean_utterance_ids(
    utterance_ids: list[str] | None,
) -> list[str]:
    return list(
        dict.fromkeys(
            item.strip() for item in (utterance_ids or []) if item.strip()
        )
    )


def _event_utterance_ids(
    event: AutomationEventMetadataCarrier,
) -> list[str]:
    values = event.utterance_ids_json
    if not isinstance(values, list):
        return []
    return _clean_utterance_ids(
        [item for item in values if isinstance(item, str)]
    )


def _event_is_visible(event: AutomationEventMetadataCarrier) -> bool:
    return not (event.metadata_json or {}).get(ACKNOWLEDGED_AT_KEY)


def _event_is_voice_unannounced(
    event: AutomationEventMetadataCarrier,
) -> bool:
    metadata = event.metadata_json or {}
    return (
        _event_is_visible(event)
        and metadata.get("source") != EDITING_JOB_FAILURE_SOURCE
        and not metadata.get(VOICE_PLAYED_AT_KEY)
    )


def _event_signature(metadata: dict) -> str:
    value = metadata.get(EVENT_SIGNATURE_KEY)
    return (
        value.strip()
        if isinstance(value, str) and value.strip()
        else ""
    )


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
        )
        if key in existing
    }
