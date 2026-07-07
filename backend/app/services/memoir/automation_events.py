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
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookPlan,
    MemoirBookPlanChapter,
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
from app.services.memoir.automation_event_audit_snapshots import audit_snapshot_metadata_by_event
from app.services.memoir.automation_event_metadata import (
    ACKNOWLEDGED_AT_KEY,
    ACKNOWLEDGED_BY_USER_KEY,
    AUDIT_SNAPSHOT_KEY,
    CHAPTER_AUTO_REVISED_REASON,
    DELETED_UTTERANCE_IDS_KEY,
    EVENT_SIGNATURE_KEY,
    PENDING_SOURCE_UTTERANCE_IDS_KEY,
    RESOLVED_AT_KEY,
    RESOLVED_BY_EVENT_ID_KEY,
    RESOLVED_CHAPTER_ID_KEY,
    RESOLVED_CHAPTER_TITLE_KEY,
    RESOLVED_RESULT_VERSION_ID_KEY,
    RESOLVED_UTTERANCE_IDS_KEY,
    RESOLUTION_KEY,
    RESOLUTION_REASON_KEY,
    ROUTES_KEY,
    ROUTE_KEY,
    SOURCE_ASSIGNED_REASON,
    SOURCE_DELETED_AT_KEY,
    SOURCE_DELETED_REASON,
    UTTERANCE_ID_KEY,
    VOICE_PLAYED_AT_KEY,
    VOICE_PLAYED_MESSAGE_ID_KEY,
    VOICE_PLAYED_SESSION_ID_KEY,
    VOICE_SELECTED_AT_KEY,
    VOICE_SELECTED_MESSAGE_ID_KEY,
    VOICE_SELECTED_SESSION_ID_KEY,
    _clean_unknown_utterance_ids,
    _clean_utterance_ids,
    _event_is_visible,
    _event_is_voice_unannounced,
    _event_semantic_signature,
    _event_signature,
    _event_utterance_ids,
    _metadata_utterance_ids,
    _metadata_without_deleted_utterances,
    _preserved_metadata,
    _without_deleted_ids,
)


@dataclass(frozen=True)
class AutomationEventSourceCleanupResult:
    updated_event_count: int = 0
    deleted_utterance_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutomationEventSourceReviewResolutionResult:
    updated_event_count: int = 0
    resolved_utterance_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutomationEventChapterAuditResolutionResult:
    updated_event_count: int = 0
    resolved_chapter_id: str = ""


@dataclass(frozen=True)
class AssignedSourceChapter:
    chapter_id: str
    chapter_title: str


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    statement = select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id)
    if confirmed_plan_only:
        statement = statement.where(MemoirBookPlan.status == MemoirBookPlanStatus.CONFIRMED.value)
    result = await db.execute(
        statement
    )
    assigned: dict[str, AssignedSourceChapter] = {}
    for plan in result.scalars().all():
        chapters = await _load_plan_chapters(db, plan)
        refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
        for chapter in chapters:
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


async def _load_plan_chapters(db: AsyncSession, plan: MemoirBookPlan) -> list[MemoirBookPlanChapter]:
    return (
        await db.execute(
            select(MemoirBookPlanChapter)
            .where(
                MemoirBookPlanChapter.project_id == plan.project_id,
                MemoirBookPlanChapter.plan_id == plan.id,
            )
            .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
        )
    ).scalars().all()


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
    )
    chapter_by_id: dict[str, MemoirBookPlanChapter] = {}
    assigned_by_project_utterance: dict[tuple[str, str], AssignedSourceChapter] = {}
    for plan in plan_result.scalars().all():
        chapters = await _load_plan_chapters(db, plan)
        refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
        for chapter in chapters:
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


def _metadata_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _metadata_chapter_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _audit_event_matches_revised_chapter(event: MemoirAutomationEvent, *, chapter_id: str) -> bool:
    if event.event_type != MemoirAutomationEventType.AUDIT_COMPLETED.value:
        return False
    if not _event_is_visible(event):
        return False

    metadata = _metadata_dict(event.metadata_json)
    if metadata.get("source") != "book_quality_audit":
        return False

    primary_action = _metadata_dict(metadata.get("primary_action"))
    action_chapter_id = _metadata_chapter_id(primary_action.get("chapter_id"))
    if primary_action.get("type") == "revise_chapter" and action_chapter_id == chapter_id:
        return metadata.get("overall_status") == "needs_updates"

    primary_chapter = _metadata_dict(metadata.get("primary_chapter"))
    return (
        _metadata_chapter_id(primary_chapter.get("chapter_id")) == chapter_id
        and primary_chapter.get("status") == "needs_update"
    )


def _apply_revised_chapter_audit_resolution(
    event: MemoirAutomationEvent,
    *,
    chapter_id: str,
    now_iso: str,
    reason: str,
    revision_event_id: str | None,
    result_version_id: str | None,
) -> bool:
    if not _audit_event_matches_revised_chapter(event, chapter_id=chapter_id):
        return False

    metadata = dict(event.metadata_json or {})
    metadata[RESOLUTION_KEY] = "confirmed"
    metadata[RESOLUTION_REASON_KEY] = reason
    metadata[RESOLVED_AT_KEY] = now_iso
    metadata[RESOLVED_CHAPTER_ID_KEY] = chapter_id
    if revision_event_id:
        metadata[RESOLVED_BY_EVENT_ID_KEY] = revision_event_id
    if result_version_id:
        metadata[RESOLVED_RESULT_VERSION_ID_KEY] = result_version_id
    event.metadata_json = metadata
    return True


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


def _action_required_event_priority(event: MemoirAutomationEvent) -> int:
    status = event.status
    if status == MemoirAutomationEventStatus.FAILED.value:
        return 0
    if status == MemoirAutomationEventStatus.NEEDS_REVIEW.value:
        return 1
    if status == MemoirAutomationEventStatus.PENDING.value:
        return 2
    return 3


def _prioritized_events(events: list[MemoirAutomationEvent]) -> list[MemoirAutomationEvent]:
    return sorted(events, key=_action_required_event_priority)


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


async def resolve_revised_chapter_audit_events(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    revision_event_id: str | None = None,
    result_version_id: str | None = None,
    reason: str = CHAPTER_AUTO_REVISED_REASON,
) -> AutomationEventChapterAuditResolutionResult:
    if not chapter_id:
        return AutomationEventChapterAuditResolutionResult()

    result = await db.execute(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
        )
    )
    now_iso = _utcnow_iso()
    updated_count = 0
    for event in result.scalars().all():
        if _apply_revised_chapter_audit_resolution(
            event,
            chapter_id=chapter_id,
            now_iso=now_iso,
            reason=reason,
            revision_event_id=revision_event_id,
            result_version_id=result_version_id,
        ):
            db.add(event)
            updated_count += 1
    if updated_count:
        await db.flush()
    return AutomationEventChapterAuditResolutionResult(
        updated_event_count=updated_count,
        resolved_chapter_id=chapter_id if updated_count else "",
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
    events = _prioritized_events(events)
    items = events[:limit]
    details_by_event = await _utterance_details_by_event(db, items)
    audit_snapshots_by_event = await audit_snapshot_metadata_by_event(db, items)
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
