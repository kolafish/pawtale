from __future__ import annotations

from typing import Any

from app.services.memoir.semantic_state import semantic_json_signature


BOOK_EDITORIAL_REVISION_APPLICATION_KEY = "book_editorial_revision_application"
BOOK_EDITORIAL_ACTION_TYPES = {
    "remove_repetition",
    "improve_transition",
    "reorder_chapters",
    "revise_book_profile",
}
BOOK_METADATA_FIELDS = {
    "title",
    "subtitle",
    "narrator_name",
    "prepared_by",
    "preface",
    "afterword",
    "back_cover_quote",
}


def action_id(action: dict[str, object]) -> str:
    return semantic_json_signature(action)


def review_actions(review_json: object) -> list[dict[str, Any]]:
    if not isinstance(review_json, dict):
        return []
    value = review_json.get("editorial_actions")
    return (
        [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def selected_review_actions(
    review_json: object,
    action_ids: list[str],
) -> list[dict[str, Any]]:
    requested = list(dict.fromkeys(action_ids))
    by_id = {
        str(action.get("id") or ""): action
        for action in review_actions(review_json)
        if str(action.get("id") or "")
    }
    selected = [by_id[action_id] for action_id in requested if action_id in by_id]
    if not selected or len(selected) != len(requested):
        raise ValueError("所选编辑建议已经变化，请重新选择")
    return selected


def actions_signature(actions: list[dict[str, Any]]) -> str:
    return semantic_json_signature(actions)


def metadata_fields(actions: list[dict[str, Any]]) -> set[str]:
    return {
        str(field)
        for action in actions
        if action.get("action_type") == "revise_book_profile"
        for field in action.get("metadata_fields", [])
        if str(field) in BOOK_METADATA_FIELDS
    }


def application_marker(output: object) -> dict[str, Any]:
    metadata = getattr(output, "metadata_json", None)
    marker = (
        metadata.get(BOOK_EDITORIAL_REVISION_APPLICATION_KEY)
        if isinstance(metadata, dict)
        else None
    )
    return marker if isinstance(marker, dict) else {}


def marker_matches_review(
    *,
    output: object,
    version: object,
    review: object,
    answers_signature: str,
) -> bool:
    marker = application_marker(output)
    raw_action_ids = marker.get("action_ids")
    try:
        actions = selected_review_actions(
            getattr(review, "review_json", None),
            (
                [str(value) for value in raw_action_ids]
                if isinstance(raw_action_ids, list)
                else []
            ),
        )
    except ValueError:
        return False
    return bool(
        version is not None
        and marker.get("review_input_signature")
        == getattr(review, "input_signature", None)
        and marker.get("actions_signature") == actions_signature(actions)
        and marker.get("answers_signature", "") == answers_signature
        and marker.get("output_version_id") == getattr(version, "id", None)
    )
