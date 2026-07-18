from __future__ import annotations

from types import SimpleNamespace

from app.services.memoir import automation_event_metadata as metadata


def _event(
    *,
    utterance_ids_json: list[str] | None = None,
    metadata_json: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        utterance_ids_json=utterance_ids_json or [],
        metadata_json=metadata_json or {},
    )


def test_event_utterance_ids_uses_explicit_ids_and_deduplicates():
    event = _event(utterance_ids_json=[" u1 ", "u2", "u1", ""])

    assert metadata._event_utterance_ids(event) == ["u1", "u2"]


def test_preserved_metadata_contains_only_user_and_voice_state():
    preserved = metadata._preserved_metadata(
        {
            metadata.ACKNOWLEDGED_AT_KEY: "2026-07-18T10:00:00+00:00",
            metadata.ACKNOWLEDGED_BY_USER_KEY: True,
            metadata.VOICE_PLAYED_SESSION_ID_KEY: "session-1",
            "legacy_route": {"utterance_id": "u1"},
        }
    )

    assert preserved == {
        metadata.ACKNOWLEDGED_AT_KEY: "2026-07-18T10:00:00+00:00",
        metadata.ACKNOWLEDGED_BY_USER_KEY: True,
        metadata.VOICE_PLAYED_SESSION_ID_KEY: "session-1",
    }
