from __future__ import annotations

from types import SimpleNamespace

from app.domain.memoir.types import MemoirAutomationEventType
from app.services.memoir import automation_event_metadata as metadata


def _event(
    *,
    event_type: MemoirAutomationEventType = MemoirAutomationEventType.SOURCE_ASSIGNED,
    chapter_id: str | None = "chapter-1",
    utterance_ids_json: list[str] | None = None,
    metadata_json: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        event_type=event_type.value,
        chapter_id=chapter_id,
        utterance_ids_json=utterance_ids_json or [],
        metadata_json=metadata_json or {},
    )


def test_event_utterance_ids_collects_metadata_routes_and_deduplicates():
    event = _event(
        utterance_ids_json=[" u1 ", "u2", "u1"],
        metadata_json={
            metadata.UTTERANCE_ID_KEY: "u3",
            metadata.ROUTE_KEY: {metadata.UTTERANCE_ID_KEY: "u2"},
            metadata.ROUTES_KEY: [
                {metadata.UTTERANCE_ID_KEY: "u4"},
                {metadata.UTTERANCE_ID_KEY: " "},
                "ignored",
            ],
            metadata.PENDING_SOURCE_UTTERANCE_IDS_KEY: ["u5", "u4", 12],
        },
    )

    assert metadata._event_utterance_ids(event) == ["u1", "u2", "u3", "u4", "u5"]


def test_metadata_without_deleted_utterances_cleans_all_reference_shapes():
    cleaned = metadata._metadata_without_deleted_utterances(
        {
            metadata.UTTERANCE_ID_KEY: "u1",
            metadata.ROUTE_KEY: {metadata.UTTERANCE_ID_KEY: "u2", "source_index": 1},
            metadata.ROUTES_KEY: [
                {metadata.UTTERANCE_ID_KEY: "u3", "source_index": 2},
                {metadata.UTTERANCE_ID_KEY: "u4", "source_index": 3},
                "kept",
            ],
            metadata.PENDING_SOURCE_UTTERANCE_IDS_KEY: ["u1", "u5", "u6"],
        },
        {"u1", "u3", "u5"},
    )

    assert metadata.UTTERANCE_ID_KEY not in cleaned
    assert cleaned[metadata.ROUTE_KEY] == {metadata.UTTERANCE_ID_KEY: "u2", "source_index": 1}
    assert cleaned[metadata.ROUTES_KEY] == [
        {metadata.UTTERANCE_ID_KEY: "u4", "source_index": 3},
        "kept",
    ]
    assert cleaned[metadata.PENDING_SOURCE_UTTERANCE_IDS_KEY] == ["u6"]


def test_event_semantic_signature_prefers_explicit_signature_and_keeps_legacy_fallbacks():
    explicit = _event(metadata_json={metadata.EVENT_SIGNATURE_KEY: " signature-1 "})
    auto_assigned = _event(
        event_type=MemoirAutomationEventType.SOURCE_ASSIGNED,
        chapter_id="chapter-2",
        utterance_ids_json=["u2", "u1"],
        metadata_json={"source": "auto_assignment"},
    )
    auto_revised = _event(
        event_type=MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
        chapter_id="chapter-3",
        utterance_ids_json=["fallback"],
        metadata_json={
            "source": "book_auto_revision",
            metadata.PENDING_SOURCE_UTTERANCE_IDS_KEY: ["u9", "u8"],
        },
    )

    assert metadata._event_semantic_signature(explicit) == "signature-1"
    assert metadata._event_semantic_signature(auto_assigned) == "source_assigned:chapter-2:u1,u2"
    assert metadata._event_semantic_signature(auto_revised) == "book_auto_revision:chapter-3:u8,u9"
