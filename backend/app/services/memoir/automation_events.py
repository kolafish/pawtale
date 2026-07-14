from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirEditingJob,
)
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirAutomationEventListResponse,
    MemoirAutomationEventResponse,
    MemoirAutomationEventUtteranceDetailResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.automation_event_audit_snapshots import audit_snapshot_metadata_by_event
from app.services.memoir.automation_event_metadata import (
    ACKNOWLEDGED_AT_KEY,
    ACKNOWLEDGED_BY_USER_KEY,
    AUDIT_SNAPSHOT_KEY,
    EDITING_JOB_FAILURE_SOURCE,
    EVENT_SIGNATURE_KEY,
    VOICE_PLAYED_AT_KEY,
    VOICE_PLAYED_MESSAGE_ID_KEY,
    VOICE_PLAYED_SESSION_ID_KEY,
    VOICE_SELECTED_AT_KEY,
    VOICE_SELECTED_MESSAGE_ID_KEY,
    VOICE_SELECTED_SESSION_ID_KEY,
    _clean_utterance_ids,
    _event_is_visible,
    _event_is_voice_unannounced,
    _event_signature,
    _preserved_metadata,
)
from app.services.memoir.automation_event_sources import utterance_details_by_event


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


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
        if _event_signature(event.metadata_json or {}) != signature:
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
        for signature in [_event_signature(event.metadata_json or {})]
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

        signature = _event_signature(event.metadata_json or {})
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


async def _without_recovered_job_failures(
    db: AsyncSession,
    events: list[MemoirAutomationEvent],
) -> list[MemoirAutomationEvent]:
    job_ids = {
        str(metadata.get("editing_job_id") or "").strip()
        for event in events
        for metadata in [event.metadata_json or {}]
        if metadata.get("source") == EDITING_JOB_FAILURE_SOURCE
    }
    job_ids.discard("")
    if not job_ids:
        return events
    succeeded_job_ids = set(
        (
            await db.scalars(
                select(MemoirEditingJob.id).where(
                    MemoirEditingJob.id.in_(job_ids),
                    MemoirEditingJob.status == MemoirEditingJobStatus.SUCCEEDED.value,
                )
            )
        ).all()
    )
    if not succeeded_job_ids:
        return events
    return [
        event
        for event in events
        if str((event.metadata_json or {}).get("editing_job_id") or "").strip()
        not in succeeded_job_ids
    ]


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
    event_signature: str,
    status: MemoirAutomationEventStatus = MemoirAutomationEventStatus.COMPLETED,
    chapter_id: str | None = None,
    utterance_ids: list[str] | None = None,
    confidence: float | None = None,
    metadata_json: dict | None = None,
) -> MemoirAutomationEvent:
    signature = event_signature.strip()
    if not signature:
        raise ValueError("event_signature must not be empty")
    metadata = {
        **dict(metadata_json or {}),
        EVENT_SIGNATURE_KEY: signature,
    }
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
    events = await _without_recovered_job_failures(db, events)
    events = _visible_events(
        events,
        include_acknowledged=include_acknowledged,
        voice_unannounced_only=voice_unannounced_only,
    )
    events = _prioritized_events(events)
    items = events[:limit]
    details_by_event = await utterance_details_by_event(db, items)
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
