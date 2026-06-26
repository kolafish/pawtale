from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirAutomationEventStatus, MemoirAutomationEventType
from app.models.memoir import MemoirAutomationEvent
from app.models.projects import Project
from app.schemas.memoir_voice import MemoirAutomationEventListResponse, MemoirAutomationEventResponse
from app.services.memoir.access import load_memoir_project

ACKNOWLEDGED_AT_KEY = "acknowledged_at"
ACKNOWLEDGED_BY_USER_KEY = "acknowledged_by_user"
RESOLUTION_KEY = "resolution"
VOICE_ANNOUNCED_AT_KEY = "voice_announced_at"
VOICE_ANNOUNCED_SESSION_ID_KEY = "voice_announced_session_id"
VOICE_ANNOUNCED_MESSAGE_ID_KEY = "voice_announced_message_id"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clean_utterance_ids(utterance_ids: list[str] | None) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in (utterance_ids or []) if item.strip()))


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
