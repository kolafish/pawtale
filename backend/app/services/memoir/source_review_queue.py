from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import MemoirFollowupAnswerInterpretationStatus
from app.domain.memoir.types import MemoirAutomationEventStatus, MemoirAutomationEventType
from app.models.memoir import MemoirAutomationEvent, MemoirVoiceUtterance
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
    MemoirFollowupAnswerInterpretationResponse,
)
from app.services.memoir import book_plan_write_workflow
from app.services.memoir import book_plans
from app.services.memoir import book_source_workflow
from app.services.memoir import source_router
from app.services.memoir.access import load_memoir_project
from app.services.memoir.narrative import followup_completion
from app.services.memoir.narrative import ingestion_jobs as narrative_ingestion
from app.services.memoir.source_disposition import apply_source_route_disposition


SOURCE_REVIEW_EVENT_TYPE = MemoirAutomationEventType.SOURCE_HELD
ROUTE_ACTIONS = {
    "assign_to_chapter",
    "record_as_context",
    "suggest_new_chapter",
    "hold_for_review",
    "ignore",
}
MATERIAL_TYPES = {"memoir_content", "ai_interaction", "meta_instruction", "noise", "too_short"}


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


def _material_type(metadata: dict[str, object]) -> str:
    material_type = metadata.get("material_type")
    return material_type if isinstance(material_type, str) and material_type in MATERIAL_TYPES else "memoir_content"


def _event_route_metadata(metadata: dict[str, object]) -> dict[str, object]:
    route = metadata.get("route")
    return route if isinstance(route, dict) else metadata


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
    route_metadata = _event_route_metadata(metadata)
    utterance_id = _event_utterance_id(event)
    source_index = route_metadata.get("source_index")
    target_chapter_title = route_metadata.get("target_chapter_title")
    suggested_title = route_metadata.get("suggested_title")
    reason = route_metadata.get("reason")
    raw_followup_answer = route_metadata.get("followup_answer")
    followup_answer = (
        MemoirFollowupAnswerInterpretationResponse.model_validate(raw_followup_answer)
        if isinstance(raw_followup_answer, dict)
        else None
    )
    return MemoirBookSourceReviewItemResponse(
        id=event.id,
        project_id=event.project_id,
        status=MemoirAutomationEventStatus(event.status),
        utterance_id=utterance_id,
        source_index=source_index if isinstance(source_index, int) else 0,
        material_type=_material_type(route_metadata),
        route_action=_route_action(route_metadata),
        target_chapter_id=event.chapter_id,
        target_chapter_title=target_chapter_title if isinstance(target_chapter_title, str) else None,
        suggested_title=suggested_title if isinstance(suggested_title, str) else None,
        confidence=event.confidence,
        reason=reason if isinstance(reason, str) else "",
        summary_text=event.summary_text,
        followup_answer=followup_answer,
        evidence=_evidence_items(route_metadata),
        metadata_json=metadata,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def _route_summary(route: MemoirBookSourceRouteResponse) -> str:
    if route.action == "assign_to_chapter" and route.target_chapter_title:
        return f"建议将第 {route.source_index} 条讲述归入「{route.target_chapter_title}」。"
    if route.action == "suggest_new_chapter":
        return f"建议将第 {route.source_index} 条讲述作为新章节「{route.suggested_title or '新的章节'}」。"
    if route.action == "ignore":
        return f"第 {route.source_index} 条讲述不适合收入章节。"
    return f"第 {route.source_index} 条讲述需要确认归属。"


def _route_metadata(route: MemoirBookSourceRouteResponse) -> dict[str, object]:
    return {
        "route_action": route.action,
        "material_type": route.material_type,
        "utterance_id": route.utterance_id,
        "source_index": route.source_index,
        "target_chapter_title": route.target_chapter_title or "",
        "suggested_title": route.suggested_title or "",
        "reason": route.reason,
        "followup_answer": (
            route.followup_answer.model_dump(mode="json")
            if route.followup_answer is not None
            else None
        ),
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
    if route.action == "ignore":
        raise HTTPException(status_code=400, detail="这条讲述不适合收入章节")
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
    resolved_followup_answer = None
    if (
        item.followup_answer is not None
        and item.followup_answer.status
        == MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION
    ):
        if req.followup_answer_kind is None:
            raise HTTPException(status_code=400, detail="请先确认这段回答在书中的用途")
        try:
            relation = await followup_completion.confirm_answer_interpretation(
                db,
                project_id=project_id,
                task_id=item.followup_answer.task_id,
                utterance_id=utterance_id,
                answer_kind=req.followup_answer_kind,
                direct_narrative_text=req.direct_narrative_text,
                clarification_text=req.clarification_text,
                fact_resolution_text=req.fact_resolution_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        resolved_followup_answer = MemoirFollowupAnswerInterpretationResponse(
            task_id=relation.task_id,
            status=MemoirFollowupAnswerInterpretationStatus.RESOLVED,
            answer_kind=req.followup_answer_kind,
            direct_narrative_text=relation.direct_narrative_text,
            clarification_text=relation.clarification_text,
            fact_resolution_text=relation.fact_resolution_text,
            reason=relation.interpretation_reason,
        )
    elif req.followup_answer_kind is not None:
        raise HTTPException(status_code=400, detail="该整理项不是待确认的补全回答")

    target_chapter_id = req.chapter_id or item.target_chapter_id
    records_as_context = bool(
        resolved_followup_answer
        and (
            resolved_followup_answer.clarification_text
            or resolved_followup_answer.fact_resolution_text
        )
    )
    if records_as_context:
        plan = await book_plans.get_book_plan(
            db,
            user_id=user_id,
            project_id=project_id,
        )
    elif target_chapter_id:
        plan = await book_source_workflow.assign_book_sources_to_chapters(
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
        plan = await book_plan_write_workflow.apply_book_plan_structure_command(
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

    if resolved_followup_answer is not None:
        utterance = await db.scalar(
            select(MemoirVoiceUtterance)
            .where(MemoirVoiceUtterance.id == utterance_id)
            .options(selectinload(MemoirVoiceUtterance.chat_message))
        )
        if utterance is None:
            raise HTTPException(status_code=404, detail="讲述记录不存在")
        confirmed_action = "record_as_context" if records_as_context else "assign_to_chapter"
        confirmed_route = MemoirBookSourceRouteResponse(
            material_type="memoir_content",
            action=confirmed_action,
            utterance_id=utterance_id,
            source_index=item.source_index,
            target_chapter_id=resolved_chapter_id,
            target_chapter_title=resolved_chapter_title,
            confidence=1,
            reason="用户已确认补全回答用途。",
            assignment_basis="completion_task",
            followup_answer=resolved_followup_answer,
            evidence=item.evidence,
        )
        apply_source_route_disposition(utterance, confirmed_route)
        db.add(utterance)
        if resolved_followup_answer.direct_narrative_text:
            await narrative_ingestion.schedule_narrative_ingestion_job(
                db,
                project_id=project_id,
                utterance=utterance,
                source="followup_answer_confirmed",
                ai_model=req.ai_model,
            )

    event = await db.get(MemoirAutomationEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="待确认整理项不存在")
    event.chapter_id = resolved_chapter_id
    event.status = MemoirAutomationEventStatus.COMPLETED.value
    metadata = dict(event.metadata_json or {})
    route_metadata = _event_route_metadata(metadata)
    if resolved_followup_answer is not None:
        route_metadata.update(
            {
                "action": confirmed_action,
                "route_action": confirmed_action,
                "followup_answer": resolved_followup_answer.model_dump(mode="json"),
            }
        )
        if isinstance(metadata.get("route"), dict):
            metadata["route"] = route_metadata
        else:
            metadata.update(route_metadata)
    event.metadata_json = {
        **metadata,
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
