from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirAutomationEventStatus, MemoirAutomationEventType
from app.models.memoir import MemoirAutomationEvent
from app.models.projects import Project
from app.schemas.memoir_voice import MemoirAutomationEventListResponse, MemoirAutomationEventResponse
from app.services.memoir.access import load_memoir_project


def _clean_utterance_ids(utterance_ids: list[str] | None) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in (utterance_ids or []) if item.strip()))


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
) -> MemoirAutomationEventListResponse:
    await load_memoir_project(db, project_id, user_id)
    total = await db.scalar(
        select(func.count(MemoirAutomationEvent.id))
        .join(Project, MemoirAutomationEvent.project_id == Project.id)
        .where(Project.user_id == user_id, MemoirAutomationEvent.project_id == project_id)
    )
    result = await db.execute(
        select(MemoirAutomationEvent)
        .join(Project, MemoirAutomationEvent.project_id == Project.id)
        .where(Project.user_id == user_id, MemoirAutomationEvent.project_id == project_id)
        .order_by(MemoirAutomationEvent.created_at.desc(), MemoirAutomationEvent.id.desc())
        .limit(limit)
    )
    return MemoirAutomationEventListResponse(
        items=[event_response(event) for event in result.scalars().all()],
        total=total or 0,
        limit=limit,
    )


async def recent_automation_event_responses(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    limit: int = 5,
) -> list[MemoirAutomationEventResponse]:
    return (await list_automation_events(db, user_id=user_id, project_id=project_id, limit=limit)).items
