from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirAutomationEventStatus, MemoirAutomationEventType
from app.models.memoir import MemoirAutomationEvent
from app.schemas.memoir_book import (
    MemoirBookPlanResponse,
    MemoirBookPlanStructureCommandRequest,
    MemoirBookSourceAssignmentInput,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceReviewConfirmRequest,
    MemoirBookSourceReviewConfirmResponse,
    MemoirBookSourceReviewItemResponse,
    MemoirBookSourceReviewListResponse,
    MemoirBookSourceRouteEvidence,
    MemoirBookSourceRouteResponse,
)
from app.services.memoir import book_plans
from app.services.memoir import source_router
from app.services.memoir.access import load_memoir_project


SOURCE_REVIEW_EVENT_TYPE = MemoirAutomationEventType.SOURCE_HELD
ROUTE_ACTIONS = {"assign_to_chapter", "suggest_new_chapter", "hold_for_review"}


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _event_utterance_id(event: MemoirAutomationEvent) -> str:
    for item in event.utterance_ids_json or []:
        if isinstance(item, str) and item.strip():
            return item.strip()
    metadata = event.metadata_json or {}
    value = metadata.get("utterance_id")
    return value.strip() if isinstance(value, str) else ""


def _route_action(metadata: dict[str, object]) -> str:
    action = metadata.get("route_action")
    return action if isinstance(action, str) and action in ROUTE_ACTIONS else "hold_for_review"


def _evidence_items(metadata: dict[str, object]) -> list[MemoirBookSourceRouteEvidence]:
    evidence = metadata.get("evidence")
    if not isinstance(evidence, list):
        return []
    items: list[MemoirBookSourceRouteEvidence] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        try:
            items.append(MemoirBookSourceRouteEvidence.model_validate(item))
        except ValueError:
            continue
    return items


def source_review_item_response(event: MemoirAutomationEvent) -> MemoirBookSourceReviewItemResponse:
    metadata = event.metadata_json or {}
    utterance_id = _event_utterance_id(event)
    source_index = metadata.get("source_index")
    target_chapter_title = metadata.get("target_chapter_title")
    suggested_title = metadata.get("suggested_title")
    reason = metadata.get("reason")
    return MemoirBookSourceReviewItemResponse(
        id=event.id,
        project_id=event.project_id,
        status=MemoirAutomationEventStatus(event.status),
        utterance_id=utterance_id,
        source_index=source_index if isinstance(source_index, int) else 0,
        route_action=_route_action(metadata),
        target_chapter_id=event.chapter_id,
        target_chapter_title=target_chapter_title if isinstance(target_chapter_title, str) else None,
        suggested_title=suggested_title if isinstance(suggested_title, str) else None,
        confidence=event.confidence,
        reason=reason if isinstance(reason, str) else "",
        summary_text=event.summary_text,
        evidence=_evidence_items(metadata),
        metadata_json=metadata,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def _route_summary(route: MemoirBookSourceRouteResponse) -> str:
    if route.action == "assign_to_chapter" and route.target_chapter_title:
        return f"建议将第 {route.source_index} 条讲述归入「{route.target_chapter_title}」。"
    if route.action == "suggest_new_chapter":
        return f"建议将第 {route.source_index} 条讲述作为新章节「{route.suggested_title or '新的章节'}」。"
    return f"第 {route.source_index} 条讲述需要确认归属。"


def _route_metadata(route: MemoirBookSourceRouteResponse) -> dict[str, object]:
    return {
        "route_action": route.action,
        "utterance_id": route.utterance_id,
        "source_index": route.source_index,
        "target_chapter_title": route.target_chapter_title or "",
        "suggested_title": route.suggested_title or "",
        "reason": route.reason,
        "evidence": [item.model_dump(mode="json") for item in route.evidence],
    }


def _resolved_chapter_from_plan(
    plan: MemoirBookPlanResponse,
    *,
    utterance_id: str,
    preferred_chapter_id: str | None,
) -> tuple[str | None, str | None]:
    if preferred_chapter_id:
        chapter = next((item for item in plan.chapters if item.id == preferred_chapter_id), None)
        return (chapter.id, chapter.title) if chapter else (preferred_chapter_id, None)
    for chapter in plan.chapters:
        if any(ref.utterance_id == utterance_id for ref in chapter.source_refs):
            return chapter.id, chapter.title
    return None, None


async def _load_pending_review_for_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
) -> MemoirAutomationEvent | None:
    events = (
        await db.execute(
            select(MemoirAutomationEvent)
            .where(
                MemoirAutomationEvent.project_id == project_id,
                MemoirAutomationEvent.event_type == SOURCE_REVIEW_EVENT_TYPE.value,
                MemoirAutomationEvent.status == MemoirAutomationEventStatus.NEEDS_REVIEW.value,
            )
            .order_by(MemoirAutomationEvent.created_at.desc(), MemoirAutomationEvent.id.desc())
        )
    ).scalars().all()
    return next((event for event in events if _event_utterance_id(event) == utterance_id), None)


async def create_source_review_item(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    utterance_id: str,
) -> MemoirBookSourceReviewItemResponse:
    route = await source_router.route_book_source(
        db,
        user_id=user_id,
        project_id=project_id,
        utterance_id=utterance_id,
    )
    existing = await _load_pending_review_for_utterance(db, project_id=project_id, utterance_id=utterance_id)
    if existing:
        existing.chapter_id = route.target_chapter_id
        existing.summary_text = _route_summary(route)
        existing.confidence = route.confidence
        existing.metadata_json = {
            **(existing.metadata_json or {}),
            **_route_metadata(route),
            "refreshed_at": _utcnow_iso(),
        }
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
        return source_review_item_response(existing)

    event = MemoirAutomationEvent(
        project_id=project_id,
        chapter_id=route.target_chapter_id,
        event_type=SOURCE_REVIEW_EVENT_TYPE.value,
        status=MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        utterance_ids_json=[utterance_id],
        summary_text=_route_summary(route),
        confidence=route.confidence,
        metadata_json=_route_metadata(route),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return source_review_item_response(event)


async def list_source_review_items(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    include_resolved: bool = False,
) -> MemoirBookSourceReviewListResponse:
    await load_memoir_project(db, project_id, user_id)
    conditions = [
        MemoirAutomationEvent.project_id == project_id,
        MemoirAutomationEvent.event_type == SOURCE_REVIEW_EVENT_TYPE.value,
    ]
    if not include_resolved:
        conditions.append(MemoirAutomationEvent.status == MemoirAutomationEventStatus.NEEDS_REVIEW.value)
    events = (
        await db.execute(
            select(MemoirAutomationEvent)
            .where(*conditions)
            .order_by(MemoirAutomationEvent.created_at.desc(), MemoirAutomationEvent.id.desc())
        )
    ).scalars().all()
    return MemoirBookSourceReviewListResponse(
        items=[source_review_item_response(event) for event in events],
        total=len(events),
    )


async def _load_review_event(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    event_id: str,
) -> MemoirAutomationEvent:
    await load_memoir_project(db, project_id, user_id)
    event = await db.get(MemoirAutomationEvent, event_id)
    if not event or event.project_id != project_id or event.event_type != SOURCE_REVIEW_EVENT_TYPE.value:
        raise HTTPException(status_code=404, detail="待确认整理项不存在")
    if event.status != MemoirAutomationEventStatus.NEEDS_REVIEW.value:
        raise HTTPException(status_code=400, detail="待确认整理项已处理")
    if not _event_utterance_id(event):
        raise HTTPException(status_code=400, detail="待确认整理项缺少讲述记录")
    return event


async def confirm_source_review_item(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    event_id: str,
    req: MemoirBookSourceReviewConfirmRequest,
) -> MemoirBookSourceReviewConfirmResponse:
    event = await _load_review_event(db, user_id=user_id, project_id=project_id, event_id=event_id)
    item = source_review_item_response(event)
    utterance_id = item.utterance_id
    target_chapter_id = req.chapter_id or item.target_chapter_id
    if target_chapter_id:
        plan = await book_plans.assign_book_sources_to_chapters(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookSourceAssignmentRequest(
                ai_model=req.ai_model,
                assignments=[
                    MemoirBookSourceAssignmentInput(
                        chapter_id=target_chapter_id,
                        utterance_ids=[utterance_id],
                    )
                ],
            ),
        )
    else:
        title = req.create_new_chapter_title or item.suggested_title
        if not title:
            raise HTTPException(status_code=400, detail="缺少目标章节")
        plan = await book_plans.apply_book_plan_structure_command(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookPlanStructureCommandRequest(
                type="create_chapter_from_source",
                title=title,
                utterance_id=utterance_id,
            ),
        )

    resolved_chapter_id, resolved_chapter_title = _resolved_chapter_from_plan(
        plan,
        utterance_id=utterance_id,
        preferred_chapter_id=target_chapter_id,
    )

    event = await db.get(MemoirAutomationEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="待确认整理项不存在")
    event.chapter_id = resolved_chapter_id
    event.status = MemoirAutomationEventStatus.COMPLETED.value
    event.metadata_json = {
        **(event.metadata_json or {}),
        "resolution": "confirmed",
        "resolved_at": _utcnow_iso(),
        "resolved_chapter_id": resolved_chapter_id or "",
        "resolved_chapter_title": resolved_chapter_title or "",
    }
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return MemoirBookSourceReviewConfirmResponse(
        item=source_review_item_response(event),
        plan=plan,
    )


async def ignore_source_review_item(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    event_id: str,
) -> MemoirBookSourceReviewItemResponse:
    event = await _load_review_event(db, user_id=user_id, project_id=project_id, event_id=event_id)
    event.status = MemoirAutomationEventStatus.COMPLETED.value
    event.metadata_json = {
        **(event.metadata_json or {}),
        "resolution": "ignored",
        "resolved_at": _utcnow_iso(),
    }
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return source_review_item_response(event)
