from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookPlanStatus,
)
from app.models.memoir import MemoirAutomationEvent, MemoirBookPlan, MemoirBookPlanChapter, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_voice import MemoirAutomationEventListResponse, MemoirAutomationEventResponse
from app.services.memoir.access import load_memoir_project

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
VOICE_ANNOUNCED_AT_KEY = "voice_announced_at"
VOICE_ANNOUNCED_SESSION_ID_KEY = "voice_announced_session_id"
VOICE_ANNOUNCED_MESSAGE_ID_KEY = "voice_announced_message_id"
ROUTE_KEY = "route"
ROUTES_KEY = "routes"
PENDING_SOURCE_UTTERANCE_IDS_KEY = "pending_source_utterance_ids"
UTTERANCE_ID_KEY = "utterance_id"


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


def _source_ref_utterance_id(ref: object) -> str:
    if not isinstance(ref, dict):
        return ""
    value = ref.get(UTTERANCE_ID_KEY)
    return value.strip() if isinstance(value, str) else ""


async def _assigned_source_chapters(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: set[str] | None = None,
    confirmed_plan_only: bool = False,
) -> dict[str, AssignedSourceChapter]:
    statement = select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.project_id == project_id)
    if confirmed_plan_only:
        statement = statement.join(
            MemoirBookPlan,
            MemoirBookPlanChapter.plan_id == MemoirBookPlan.id,
        ).where(MemoirBookPlan.status == MemoirBookPlanStatus.CONFIRMED.value)
    result = await db.execute(
        statement
    )
    assigned: dict[str, AssignedSourceChapter] = {}
    for chapter in result.scalars().all():
        for ref in chapter.source_refs_json or []:
            utterance_id = _source_ref_utterance_id(ref)
            if not utterance_id or utterance_id in assigned:
                continue
            if utterance_ids is not None and utterance_id not in utterance_ids:
                continue
            assigned[utterance_id] = AssignedSourceChapter(
                chapter_id=chapter.id,
                chapter_title=chapter.title,
            )
    return assigned


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
    return _event_is_visible(event) and not metadata.get(VOICE_ANNOUNCED_AT_KEY)


def event_response(event: MemoirAutomationEvent) -> MemoirAutomationEventResponse:
    return MemoirAutomationEventResponse(
        id=event.id,
        project_id=event.project_id,
        event_type=MemoirAutomationEventType(event.event_type),
        status=MemoirAutomationEventStatus(event.status),
        chapter_id=event.chapter_id,
        utterance_ids=_clean_utterance_ids(event.utterance_ids_json),
        summary_text=event.summary_text,
        confidence=event.confidence,
        metadata_json=event.metadata_json or {},
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
    if voice_unannounced_only:
        events = [event for event in events if _event_is_voice_unannounced(event)]
    elif not include_acknowledged:
        events = [event for event in events if _event_is_visible(event)]
    items = events[:limit]
    return MemoirAutomationEventListResponse(
        items=[event_response(event) for event in items],
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


async def mark_automation_events_voice_announced(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    event_ids: list[str],
    voice_session_id: str,
    message_id: str,
) -> None:
    clean_ids = _clean_utterance_ids(event_ids)
    if not clean_ids:
        return
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.id.in_(clean_ids),
        )
    )
    announced_at = _utcnow_iso()
    for event in result.scalars().all():
        metadata = event.metadata_json or {}
        if metadata.get(VOICE_ANNOUNCED_AT_KEY):
            continue
        event.metadata_json = {
            **metadata,
            VOICE_ANNOUNCED_AT_KEY: announced_at,
            VOICE_ANNOUNCED_SESSION_ID_KEY: voice_session_id,
            VOICE_ANNOUNCED_MESSAGE_ID_KEY: message_id,
        }
        db.add(event)
    await db.commit()


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
