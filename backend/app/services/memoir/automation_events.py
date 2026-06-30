from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookPlanStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirAutomationEventListResponse,
    MemoirAutomationEventResponse,
    MemoirAutomationEventUtteranceDetailResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_source_links import (
    plan_source_ref_dicts_by_chapter,
    source_ref_utterance_id,
)

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


@dataclass(frozen=True)
class AutomationEventSourceCleanupResult:
    updated_event_count: int = 0
    deleted_utterance_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutomationEventSourceReviewResolutionResult:
    updated_event_count: int = 0
    resolved_utterance_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssignedSourceChapter:
    chapter_id: str
    chapter_title: str


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def _event_utterance_ids(event: MemoirAutomationEvent) -> list[str]:
    return _clean_utterance_ids(
        _clean_unknown_utterance_ids(event.utterance_ids_json)
        + _metadata_utterance_ids(event.metadata_json or {})
    )


def _display_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()


def _preview_text(text: str, limit: int = 72) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def _metadata_source_index(metadata: dict, utterance_id: str) -> int | None:
    route = metadata.get(ROUTE_KEY)
    if isinstance(route, dict) and route.get(UTTERANCE_ID_KEY) == utterance_id:
        source_index = route.get("source_index")
        return source_index if isinstance(source_index, int) and source_index > 0 else None

    routes = metadata.get(ROUTES_KEY)
    if isinstance(routes, list):
        for route_item in routes:
            if not isinstance(route_item, dict) or route_item.get(UTTERANCE_ID_KEY) != utterance_id:
                continue
            source_index = route_item.get("source_index")
            return source_index if isinstance(source_index, int) and source_index > 0 else None
    return None


async def _assigned_source_chapters(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: set[str] | None = None,
    confirmed_plan_only: bool = False,
) -> dict[str, AssignedSourceChapter]:
    statement = (
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(selectinload(MemoirBookPlan.chapters))
    )
    if confirmed_plan_only:
        statement = statement.where(MemoirBookPlan.status == MemoirBookPlanStatus.CONFIRMED.value)
    result = await db.execute(
        statement
    )
    assigned: dict[str, AssignedSourceChapter] = {}
    for plan in result.scalars().all():
        refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
        for chapter in sorted(plan.chapters, key=lambda item: (item.sort_order, item.id)):
            for ref in refs_by_chapter.get(chapter.id, []):
                utterance_id = source_ref_utterance_id(ref)
                if not utterance_id or utterance_id in assigned:
                    continue
                if utterance_ids is not None and utterance_id not in utterance_ids:
                    continue
                assigned[utterance_id] = AssignedSourceChapter(
                    chapter_id=chapter.id,
                    chapter_title=chapter.title,
                )
    return assigned


async def _utterance_details_by_event(
    db: AsyncSession,
    events: list[MemoirAutomationEvent],
) -> dict[str, list[MemoirAutomationEventUtteranceDetailResponse]]:
    event_utterance_ids = {
        event.id: _event_utterance_ids(event)
        for event in events
    }
    utterance_ids = _clean_utterance_ids(
        [utterance_id for ids in event_utterance_ids.values() for utterance_id in ids]
    )
    if not utterance_ids:
        return {event.id: [] for event in events}

    utterance_result = await db.execute(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id.in_(utterance_ids))
    )
    utterances = {utterance.id: utterance for utterance in utterance_result.scalars().all()}

    project_ids = {event.project_id for event in events}
    plan_result = await db.execute(
        select(MemoirBookPlan)
        .where(
            MemoirBookPlan.project_id.in_(sorted(project_ids)),
            MemoirBookPlan.status == MemoirBookPlanStatus.CONFIRMED.value,
        )
        .options(selectinload(MemoirBookPlan.chapters))
    )
    chapter_by_id: dict[str, MemoirBookPlanChapter] = {}
    assigned_by_project_utterance: dict[tuple[str, str], AssignedSourceChapter] = {}
    for plan in plan_result.scalars().all():
        refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
        for chapter in plan.chapters:
            chapter_by_id[chapter.id] = chapter
            for ref in refs_by_chapter.get(chapter.id, []):
                utterance_id = source_ref_utterance_id(ref)
                if not utterance_id:
                    continue
                assigned_by_project_utterance[(chapter.project_id, utterance_id)] = AssignedSourceChapter(
                    chapter_id=chapter.id,
                    chapter_title=chapter.title,
                )

    details: dict[str, list[MemoirAutomationEventUtteranceDetailResponse]] = {}
    for event in events:
        event_details: list[MemoirAutomationEventUtteranceDetailResponse] = []
        fallback_chapter = chapter_by_id.get(event.chapter_id or "")
        for utterance_id in event_utterance_ids[event.id]:
            utterance = utterances.get(utterance_id)
            if not utterance:
                continue
            source_index = _metadata_source_index(event.metadata_json or {}, utterance_id)
            assignment = assigned_by_project_utterance.get((event.project_id, utterance_id))
            chapter_id = (
                assignment.chapter_id
                if assignment
                else (fallback_chapter.id if fallback_chapter else None)
            )
            chapter_title = (
                assignment.chapter_title
                if assignment
                else (fallback_chapter.title if fallback_chapter else None)
            )
            text = _display_text(utterance)
            record_title = f"语音记录 {source_index}" if source_index else "讲述记录"
            event_details.append(
                MemoirAutomationEventUtteranceDetailResponse(
                    utterance_id=utterance.id,
                    source_index=source_index,
                    record_title=record_title,
                    preview_text=_preview_text(text),
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    created_at=utterance.created_at,
                )
            )
        details[event.id] = event_details
    return details


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


def _quoted_title(text: str) -> str:
    match = re.search(r"「([^」]+)」", text)
    return match.group(1).strip() if match else ""


def _summary_after_source_cleanup(event: MemoirAutomationEvent, remaining_count: int) -> str:
    if remaining_count <= 0:
        return "关联讲述已删除，自动处理记录已隐藏。"
    title = _quoted_title(event.summary_text)
    if event.event_type == MemoirAutomationEventType.SOURCE_ASSIGNED.value and title:
        return f"{remaining_count} 条讲述已自动整理进「{title}」。"
    if event.event_type == MemoirAutomationEventType.CHAPTER_AUTO_REVISED.value and title:
        return f"「{title}」已自动补写 {remaining_count} 条新增讲述。"
    return event.summary_text


def _apply_deleted_utterance_cleanup(
    event: MemoirAutomationEvent,
    *,
    deleted_ids: set[str],
    now_iso: str,
) -> bool:
    event_ids = _event_utterance_ids(event)
    deleted_refs = [utterance_id for utterance_id in event_ids if utterance_id in deleted_ids]
    if not deleted_refs:
        return False

    metadata = _metadata_without_deleted_utterances(event.metadata_json or {}, deleted_ids)
    metadata[DELETED_UTTERANCE_IDS_KEY] = _clean_utterance_ids(
        _clean_unknown_utterance_ids(metadata.get(DELETED_UTTERANCE_IDS_KEY)) + deleted_refs
    )
    metadata[SOURCE_DELETED_AT_KEY] = now_iso

    remaining_direct_ids = _without_deleted_ids(event.utterance_ids_json, deleted_ids)
    remaining_ids = _clean_utterance_ids(remaining_direct_ids + _metadata_utterance_ids(metadata))
    should_hide = not remaining_ids or event.status in {
        MemoirAutomationEventStatus.PENDING.value,
        MemoirAutomationEventStatus.NEEDS_REVIEW.value,
    }

    event.utterance_ids_json = remaining_direct_ids
    event.summary_text = _summary_after_source_cleanup(event, len(remaining_ids))
    if should_hide:
        event.status = MemoirAutomationEventStatus.COMPLETED.value
        if not metadata.get(RESOLUTION_KEY):
            metadata[RESOLUTION_KEY] = "ignored"
            metadata[RESOLUTION_REASON_KEY] = SOURCE_DELETED_REASON
        metadata.setdefault("resolved_at", now_iso)
    event.metadata_json = metadata
    return True


def _apply_assigned_source_review_resolution(
    event: MemoirAutomationEvent,
    *,
    assigned_sources: dict[str, AssignedSourceChapter],
    now_iso: str,
    reason: str,
) -> bool:
    if event.event_type != MemoirAutomationEventType.SOURCE_HELD.value:
        return False
    if event.status != MemoirAutomationEventStatus.NEEDS_REVIEW.value:
        return False

    resolved_ids = [utterance_id for utterance_id in _event_utterance_ids(event) if utterance_id in assigned_sources]
    if not resolved_ids:
        return False

    resolved_source = assigned_sources[resolved_ids[0]]
    event.chapter_id = resolved_source.chapter_id
    event.status = MemoirAutomationEventStatus.COMPLETED.value
    event.summary_text = f"讲述已整理进「{resolved_source.chapter_title}」，自动处理记录已隐藏。"
    event.metadata_json = {
        **(event.metadata_json or {}),
        RESOLUTION_KEY: "confirmed",
        RESOLUTION_REASON_KEY: reason,
        RESOLVED_AT_KEY: now_iso,
        RESOLVED_CHAPTER_ID_KEY: resolved_source.chapter_id,
        RESOLVED_CHAPTER_TITLE_KEY: resolved_source.chapter_title,
        RESOLVED_UTTERANCE_IDS_KEY: resolved_ids,
    }
    return True


def _event_is_visible(event: MemoirAutomationEvent) -> bool:
    metadata = event.metadata_json or {}
    if metadata.get(ACKNOWLEDGED_AT_KEY):
        return False
    if metadata.get(RESOLUTION_KEY) in {"confirmed", "ignored"}:
        return False
    return True


def _event_is_voice_unannounced(event: MemoirAutomationEvent) -> bool:
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


def _compact_response_text(value: object, *, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip() if len(text) > limit else text


def _response_string_list(value: object, *, limit: int = 4, item_limit: int = 120) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _compact_response_text(item, limit=item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _response_gap_items(value: object, *, limit: int = 4) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        message = _compact_response_text(item.get("message"), limit=160)
        if not message:
            continue
        items.append(
            {
                "type": _compact_response_text(item.get("type"), limit=40),
                "severity": _compact_response_text(item.get("severity"), limit=20),
                "message": message,
            }
        )
        if len(items) >= limit:
            break
    return items


def _response_evidence_items(value: object, *, limit: int = 4) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        utterance_id = _compact_response_text(item.get("utterance_id"), limit=80)
        quote = _compact_response_text(item.get("quote"), limit=120)
        if not utterance_id and not quote:
            continue
        items.append({"utterance_id": utterance_id, "quote": quote})
        if len(items) >= limit:
            break
    return items


def _audit_card_response(
    card: MemoirChapterCard,
    *,
    chapter_titles: dict[str, str],
) -> dict[str, object]:
    card_json = card.card_json if isinstance(card.card_json, dict) else {}
    return {
        "id": card.id,
        "chapter_id": card.chapter_id,
        "chapter_title": chapter_titles.get(card.chapter_id, ""),
        "engine": card.engine,
        "summary": _compact_response_text(card_json.get("summary"), limit=220),
        "gaps": _response_gap_items(card_json.get("gaps")),
        "next_questions": _response_string_list(card_json.get("next_questions"), limit=6, item_limit=100),
        "source_card_ids": _response_string_list(card_json.get("source_card_ids"), limit=20, item_limit=80),
        "evidence_refs": _response_evidence_items(card_json.get("evidence_refs")),
    }


def _audit_chapter_response(
    item: object,
    *,
    chapter_titles: dict[str, str],
) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    chapter_id = _compact_response_text(item.get("chapter_id"), limit=80)
    return {
        "chapter_id": chapter_id,
        "chapter_card_id": _compact_response_text(item.get("chapter_card_id"), limit=80),
        "chapter_title": chapter_titles.get(chapter_id, ""),
        "summary": _compact_response_text(item.get("summary"), limit=220),
        "gaps": _response_gap_items(item.get("gaps")),
        "next_questions": _response_string_list(item.get("next_questions"), limit=6, item_limit=100),
        "evidence_refs": _response_evidence_items(item.get("evidence_refs")),
    }


def _audit_snapshot_response(
    snapshot: MemoirBookQualityAuditSnapshot,
    *,
    chapter_cards: list[MemoirChapterCard],
    chapter_titles: dict[str, str],
) -> dict[str, object]:
    cards = [_audit_card_response(card, chapter_titles=chapter_titles) for card in chapter_cards]
    chapter_audits = [
        item
        for item in (
            _audit_chapter_response(audit, chapter_titles=chapter_titles)
            for audit in (snapshot.chapter_audits_json or [])
        )
        if item is not None
    ]
    next_questions = _response_string_list(
        [question for card in cards for question in card.get("next_questions", [])],
        limit=6,
        item_limit=100,
    ) or _response_string_list(
        [question for audit in chapter_audits for question in audit.get("next_questions", [])],
        limit=6,
        item_limit=100,
    )
    return {
        "id": snapshot.id,
        "engine": snapshot.engine,
        "overall_status": snapshot.overall_status,
        "summary_text": _compact_response_text(snapshot.summary_text, limit=260),
        "display": snapshot.display_json or {},
        "primary_action": snapshot.primary_action_json or {},
        "next_questions": next_questions,
        "chapter_cards": cards,
        "chapter_audits": chapter_audits,
    }


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


def _event_semantic_signature(event: MemoirAutomationEvent) -> str:
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


async def _find_matching_signature_event(
    db: AsyncSession,
    *,
    project_id: str,
    event_type: MemoirAutomationEventType,
    signature: str,
) -> MemoirAutomationEvent | None:
    if not signature:
        return None
    result = await db.execute(
        select(MemoirAutomationEvent)
        .where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == event_type.value,
        )
        .order_by(MemoirAutomationEvent.created_at.desc(), MemoirAutomationEvent.id.desc())
    )
    first_match: MemoirAutomationEvent | None = None
    for event in result.scalars().all():
        if _event_semantic_signature(event) != signature:
            continue
        if not _event_is_visible(event):
            return event
        if first_match is None:
            first_match = event
    return first_match


def _visible_events(
    events: list[MemoirAutomationEvent],
    *,
    include_acknowledged: bool,
    voice_unannounced_only: bool,
) -> list[MemoirAutomationEvent]:
    if include_acknowledged and not voice_unannounced_only:
        return events

    hidden_signatures = {
        signature
        for event in events
        if not _event_is_visible(event)
        for signature in [_event_semantic_signature(event)]
        if signature
    }
    seen_signatures: set[str] = set()
    visible: list[MemoirAutomationEvent] = []
    for event in events:
        if voice_unannounced_only:
            if not _event_is_voice_unannounced(event):
                continue
        elif not _event_is_visible(event):
            continue

        signature = _event_semantic_signature(event)
        if signature and (signature in hidden_signatures or signature in seen_signatures):
            continue
        if signature:
            seen_signatures.add(signature)
        visible.append(event)
    return visible


async def _audit_snapshot_metadata_by_event(
    db: AsyncSession,
    events: list[MemoirAutomationEvent],
) -> dict[str, dict[str, object]]:
    snapshot_ids = list(dict.fromkeys(
        _metadata_string(event.metadata_json or {}, AUDIT_SNAPSHOT_ID_KEY)
        for event in events
        if event.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value
    ))
    snapshot_ids = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id]
    if not snapshot_ids:
        return {}

    project_ids = {event.project_id for event in events}
    snapshot_result = await db.execute(
        select(MemoirBookQualityAuditSnapshot).where(
            MemoirBookQualityAuditSnapshot.id.in_(snapshot_ids),
            MemoirBookQualityAuditSnapshot.project_id.in_(project_ids),
        )
    )
    snapshots = {snapshot.id: snapshot for snapshot in snapshot_result.scalars().all()}
    chapter_card_ids = list(dict.fromkeys(
        card_id
        for snapshot in snapshots.values()
        for card_id in _response_string_list(snapshot.chapter_card_ids_json, limit=50, item_limit=80)
    ))
    chapter_cards: list[MemoirChapterCard] = []
    if chapter_card_ids:
        card_result = await db.execute(
            select(MemoirChapterCard).where(
                MemoirChapterCard.id.in_(chapter_card_ids),
                MemoirChapterCard.project_id.in_(project_ids),
            )
        )
        cards_by_id = {card.id: card for card in card_result.scalars().all()}
        for card_id in chapter_card_ids:
            card = cards_by_id.get(card_id)
            if card:
                chapter_cards.append(card)

    chapter_ids = list(dict.fromkeys(
        [card.chapter_id for card in chapter_cards]
        + [
            _compact_response_text(item.get("chapter_id"), limit=80)
            for snapshot in snapshots.values()
            for item in (snapshot.chapter_audits_json or [])
            if isinstance(item, dict)
        ]
    ))
    chapter_ids = [chapter_id for chapter_id in chapter_ids if chapter_id]
    chapter_titles: dict[str, str] = {}
    if chapter_ids:
        chapter_result = await db.execute(
            select(MemoirBookPlanChapter.id, MemoirBookPlanChapter.title).where(
                MemoirBookPlanChapter.id.in_(chapter_ids),
                MemoirBookPlanChapter.project_id.in_(project_ids),
            )
        )
        chapter_titles = {chapter_id: title for chapter_id, title in chapter_result.all()}

    cards_by_snapshot_id: dict[str, list[MemoirChapterCard]] = {}
    cards_by_id = {card.id: card for card in chapter_cards}
    for snapshot in snapshots.values():
        cards_by_snapshot_id[snapshot.id] = [
            cards_by_id[card_id]
            for card_id in _response_string_list(snapshot.chapter_card_ids_json, limit=50, item_limit=80)
            if card_id in cards_by_id
        ]

    metadata_by_event: dict[str, dict[str, object]] = {}
    for event in events:
        snapshot_id = _metadata_string(event.metadata_json or {}, AUDIT_SNAPSHOT_ID_KEY)
        snapshot = snapshots.get(snapshot_id)
        if not snapshot or snapshot.project_id != event.project_id:
            continue
        metadata_by_event[event.id] = _audit_snapshot_response(
            snapshot,
            chapter_cards=cards_by_snapshot_id.get(snapshot.id, []),
            chapter_titles=chapter_titles,
        )
    return metadata_by_event


def event_response(
    event: MemoirAutomationEvent,
    utterance_details: list[MemoirAutomationEventUtteranceDetailResponse] | None = None,
    audit_snapshot: dict[str, object] | None = None,
) -> MemoirAutomationEventResponse:
    metadata = dict(event.metadata_json or {})
    if audit_snapshot:
        metadata[AUDIT_SNAPSHOT_KEY] = audit_snapshot
    return MemoirAutomationEventResponse(
        id=event.id,
        project_id=event.project_id,
        event_type=MemoirAutomationEventType(event.event_type),
        status=MemoirAutomationEventStatus(event.status),
        chapter_id=event.chapter_id,
        utterance_ids=_clean_utterance_ids(event.utterance_ids_json),
        summary_text=event.summary_text,
        confidence=event.confidence,
        metadata_json=metadata,
        utterance_details=utterance_details or [],
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


async def record_automation_event(
    db: AsyncSession,
    *,
    project_id: str,
    event_type: MemoirAutomationEventType,
    summary_text: str,
    status: MemoirAutomationEventStatus = MemoirAutomationEventStatus.COMPLETED,
    chapter_id: str | None = None,
    utterance_ids: list[str] | None = None,
    confidence: float | None = None,
    metadata_json: dict | None = None,
) -> MemoirAutomationEvent:
    event = MemoirAutomationEvent(
        project_id=project_id,
        chapter_id=chapter_id,
        event_type=event_type.value,
        status=status.value,
        utterance_ids_json=_clean_utterance_ids(utterance_ids),
        summary_text=summary_text.strip(),
        confidence=confidence,
        metadata_json=metadata_json or {},
    )
    db.add(event)
    await db.flush()
    return event


async def record_or_update_automation_event(
    db: AsyncSession,
    *,
    project_id: str,
    event_type: MemoirAutomationEventType,
    summary_text: str,
    status: MemoirAutomationEventStatus = MemoirAutomationEventStatus.COMPLETED,
    chapter_id: str | None = None,
    utterance_ids: list[str] | None = None,
    confidence: float | None = None,
    metadata_json: dict | None = None,
) -> MemoirAutomationEvent:
    metadata = dict(metadata_json or {})
    signature = _event_signature(metadata)
    existing = await _find_matching_signature_event(
        db,
        project_id=project_id,
        event_type=event_type,
        signature=signature,
    )
    if existing is None:
        return await record_automation_event(
            db,
            project_id=project_id,
            event_type=event_type,
            status=status,
            chapter_id=chapter_id,
            utterance_ids=utterance_ids,
            summary_text=summary_text,
            confidence=confidence,
            metadata_json=metadata,
        )

    existing.chapter_id = chapter_id
    existing.status = status.value
    existing.utterance_ids_json = _clean_utterance_ids(utterance_ids)
    existing.summary_text = summary_text.strip()
    existing.confidence = confidence
    existing.metadata_json = {
        **metadata,
        **_preserved_metadata(existing.metadata_json or {}),
    }
    db.add(existing)
    await db.flush()
    return existing


async def cleanup_automation_events_for_deleted_utterances(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: list[str],
) -> AutomationEventSourceCleanupResult:
    deleted_ids = set(_clean_utterance_ids(utterance_ids))
    if not deleted_ids:
        return AutomationEventSourceCleanupResult()

    result = await db.execute(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project_id)
    )
    now_iso = _utcnow_iso()
    updated_count = 0
    for event in result.scalars().all():
        if _apply_deleted_utterance_cleanup(event, deleted_ids=deleted_ids, now_iso=now_iso):
            db.add(event)
            updated_count += 1
    if updated_count:
        await db.flush()
    return AutomationEventSourceCleanupResult(
        updated_event_count=updated_count,
        deleted_utterance_ids=sorted(deleted_ids),
    )


async def cleanup_dangling_automation_event_utterances(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> AutomationEventSourceCleanupResult:
    conditions = []
    if project_id:
        conditions.append(MemoirAutomationEvent.project_id == project_id)
    result = await db.execute(select(MemoirAutomationEvent).where(*conditions))
    events = list(result.scalars().all())
    referenced_ids = _clean_utterance_ids(
        [utterance_id for event in events for utterance_id in _event_utterance_ids(event)]
    )
    if not referenced_ids:
        return AutomationEventSourceCleanupResult()

    existing_ids: set[str] = set()
    for start in range(0, len(referenced_ids), 500):
        batch = referenced_ids[start:start + 500]
        existing_result = await db.execute(
            select(MemoirVoiceUtterance.id).where(MemoirVoiceUtterance.id.in_(batch))
        )
        existing_ids.update(existing_result.scalars().all())
    deleted_ids = set(referenced_ids) - existing_ids
    if not deleted_ids:
        return AutomationEventSourceCleanupResult()

    now_iso = _utcnow_iso()
    updated_count = 0
    for event in events:
        if _apply_deleted_utterance_cleanup(event, deleted_ids=deleted_ids, now_iso=now_iso):
            db.add(event)
            updated_count += 1
    if updated_count:
        await db.flush()
    return AutomationEventSourceCleanupResult(
        updated_event_count=updated_count,
        deleted_utterance_ids=sorted(deleted_ids),
    )


async def resolve_assigned_source_review_events(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: list[str] | None = None,
    reason: str = SOURCE_ASSIGNED_REASON,
    confirmed_plan_only: bool = False,
) -> AutomationEventSourceReviewResolutionResult:
    clean_ids = set(_clean_utterance_ids(utterance_ids)) if utterance_ids is not None else None
    assigned_sources = await _assigned_source_chapters(
        db,
        project_id=project_id,
        utterance_ids=clean_ids,
        confirmed_plan_only=confirmed_plan_only,
    )
    if not assigned_sources:
        return AutomationEventSourceReviewResolutionResult()

    result = await db.execute(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
            MemoirAutomationEvent.status == MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        )
    )
    now_iso = _utcnow_iso()
    updated_count = 0
    resolved_ids: list[str] = []
    for event in result.scalars().all():
        event_ids = [utterance_id for utterance_id in _event_utterance_ids(event) if utterance_id in assigned_sources]
        if not event_ids:
            continue
        if _apply_assigned_source_review_resolution(
            event,
            assigned_sources=assigned_sources,
            now_iso=now_iso,
            reason=reason,
        ):
            db.add(event)
            updated_count += 1
            resolved_ids.extend(event_ids)
    if updated_count:
        await db.flush()
    return AutomationEventSourceReviewResolutionResult(
        updated_event_count=updated_count,
        resolved_utterance_ids=sorted(set(resolved_ids)),
    )


async def list_automation_events(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    limit: int = 10,
    include_acknowledged: bool = False,
    voice_unannounced_only: bool = False,
) -> MemoirAutomationEventListResponse:
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirAutomationEvent)
        .join(Project, MemoirAutomationEvent.project_id == Project.id)
        .where(Project.user_id == user_id, MemoirAutomationEvent.project_id == project_id)
        .order_by(MemoirAutomationEvent.created_at.desc(), MemoirAutomationEvent.id.desc())
    )
    events = list(result.scalars().all())
    events = _visible_events(
        events,
        include_acknowledged=include_acknowledged,
        voice_unannounced_only=voice_unannounced_only,
    )
    items = events[:limit]
    details_by_event = await _utterance_details_by_event(db, items)
    audit_snapshots_by_event = await _audit_snapshot_metadata_by_event(db, items)
    return MemoirAutomationEventListResponse(
        items=[
            event_response(
                event,
                details_by_event.get(event.id, []),
                audit_snapshots_by_event.get(event.id),
            )
            for event in items
        ],
        total=len(events),
        limit=limit,
    )


async def recent_automation_event_responses(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    limit: int = 5,
    voice_unannounced_only: bool = False,
) -> list[MemoirAutomationEventResponse]:
    return (
        await list_automation_events(
            db,
            user_id=user_id,
            project_id=project_id,
            limit=limit,
            voice_unannounced_only=voice_unannounced_only,
        )
    ).items


async def _mark_automation_events_voice_metadata(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    event_ids: list[str],
    voice_session_id: str,
    message_id: str,
    played: bool,
) -> int:
    clean_ids = _clean_utterance_ids(event_ids)
    if not clean_ids:
        return 0
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.id.in_(clean_ids),
        )
    )
    now_iso = _utcnow_iso()
    updated_count = 0
    for event in result.scalars().all():
        metadata = event.metadata_json or {}
        patch = {
            VOICE_SELECTED_AT_KEY: metadata.get(VOICE_SELECTED_AT_KEY) or now_iso,
            VOICE_SELECTED_SESSION_ID_KEY: voice_session_id,
            VOICE_SELECTED_MESSAGE_ID_KEY: message_id,
        }
        if played:
            patch.update(
                {
                    VOICE_PLAYED_AT_KEY: now_iso,
                    VOICE_PLAYED_SESSION_ID_KEY: voice_session_id,
                    VOICE_PLAYED_MESSAGE_ID_KEY: message_id,
                }
            )
        event.metadata_json = {**metadata, **patch}
        db.add(event)
        updated_count += 1
    await db.commit()
    return updated_count


async def mark_automation_events_voice_selected(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    event_ids: list[str],
    voice_session_id: str,
    message_id: str,
) -> int:
    return await _mark_automation_events_voice_metadata(
        db,
        user_id=user_id,
        project_id=project_id,
        event_ids=event_ids,
        voice_session_id=voice_session_id,
        message_id=message_id,
        played=False,
    )


async def mark_automation_events_voice_played(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    event_ids: list[str],
    voice_session_id: str,
    message_id: str,
) -> int:
    return await _mark_automation_events_voice_metadata(
        db,
        user_id=user_id,
        project_id=project_id,
        event_ids=event_ids,
        voice_session_id=voice_session_id,
        message_id=message_id,
        played=True,
    )


async def acknowledge_automation_event(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    event_id: str,
) -> MemoirAutomationEventResponse:
    await load_memoir_project(db, project_id, user_id)
    event = await db.get(MemoirAutomationEvent, event_id)
    if not event or event.project_id != project_id:
        raise HTTPException(status_code=404, detail="自动处理记录不存在")
    if event.status in {
        MemoirAutomationEventStatus.PENDING.value,
        MemoirAutomationEventStatus.NEEDS_REVIEW.value,
    }:
        raise HTTPException(status_code=400, detail="自动处理记录仍需处理，不能直接确认隐藏")
    event.metadata_json = {
        **(event.metadata_json or {}),
        ACKNOWLEDGED_AT_KEY: _utcnow_iso(),
        ACKNOWLEDGED_BY_USER_KEY: True,
    }
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event_response(event)
