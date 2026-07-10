from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirChapterSourceAssignedBy,
    MemoirEditingJobStatus,
)
from app.models.memoir import MemoirAutomationEvent, MemoirEditingJob, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_book import (
    MemoirBookPlanResponse,
    MemoirBookSourceAssignmentInput,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceAutoAssignmentItemResponse,
    MemoirBookSourceAutoAssignmentRunRequest,
    MemoirBookSourceAutoAssignmentRunResponse,
    MemoirBookSourceRouteResponse,
)
from app.schemas.memoir_voice import MemoirAutomationEventResponse
from app.services.memoir import automation_events
from app.services.memoir import book_quality_audit
from app.services.memoir import book_plans
from app.services.memoir import book_source_workflow
from app.services.memoir import source_cards
from app.services.memoir.chapter_source_links import ChapterSourceAssignmentMetadata
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir import source_router
from app.services.memoir.source_disposition import apply_source_route_disposition
from app.services.memoir.utterance_editing_pipeline import (
    source_routing_lineage_by_utterance,
    validate_source_routing_transcript_guard,
)


def _route_is_assignable(
    route: MemoirBookSourceRouteResponse,
    *,
    threshold: float,
) -> bool:
    return (
        route.action == "assign_to_chapter"
        and bool(route.target_chapter_id)
        and (route.confidence or 0) >= threshold
    )


def _auto_assignment_item(
    route: MemoirBookSourceRouteResponse,
    *,
    assigned: bool,
) -> MemoirBookSourceAutoAssignmentItemResponse:
    skip_reason = ""
    if not assigned:
        if route.action == "assign_to_chapter":
            skip_reason = "confidence_below_threshold"
        elif route.action == "ignore":
            skip_reason = "ignored_non_memoir_material"
        else:
            skip_reason = "not_existing_chapter_assignment"
    return MemoirBookSourceAutoAssignmentItemResponse(
        utterance_id=route.utterance_id,
        source_index=route.source_index,
        material_type=route.material_type,
        action=route.action,
        target_chapter_id=route.target_chapter_id,
        target_chapter_title=route.target_chapter_title,
        confidence=route.confidence,
        reason=route.reason,
        assigned=assigned,
        skip_reason=skip_reason,
    )


def _chapter_titles(plan: MemoirBookPlanResponse) -> dict[str, str]:
    return {chapter.id: chapter.title for chapter in plan.chapters}


def _route_metadata(
    route: MemoirBookSourceRouteResponse,
    *,
    source_lineage_by_utterance: dict[str, dict[str, object]] | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "utterance_id": route.utterance_id,
        "source_index": route.source_index,
        "material_type": route.material_type,
        "action": route.action,
        "target_chapter_id": route.target_chapter_id,
        "target_chapter_title": route.target_chapter_title,
        "suggested_title": route.suggested_title,
        "confidence": route.confidence,
        "reason": route.reason,
        "evidence": [item.model_dump(mode="json") for item in route.evidence],
    }
    lineage = (source_lineage_by_utterance or {}).get(route.utterance_id)
    if lineage:
        metadata.update(lineage)
    return metadata


def _group_assignment_inputs(
    routes: list[MemoirBookSourceRouteResponse],
    *,
    threshold: float,
) -> list[MemoirBookSourceAssignmentInput]:
    grouped: dict[str, list[str]] = {}
    for route in routes:
        if not _route_is_assignable(route, threshold=threshold) or not route.target_chapter_id:
            continue
        grouped.setdefault(route.target_chapter_id, []).append(route.utterance_id)
    return [
        MemoirBookSourceAssignmentInput(chapter_id=chapter_id, utterance_ids=utterance_ids)
        for chapter_id, utterance_ids in grouped.items()
    ]


def _assignment_link_metadata(
    routes: list[MemoirBookSourceRouteResponse],
    *,
    threshold: float,
) -> dict[str, ChapterSourceAssignmentMetadata]:
    return {
        route.utterance_id: ChapterSourceAssignmentMetadata(
            assigned_by=MemoirChapterSourceAssignedBy.AI,
            confidence=route.confidence,
            reason=route.reason,
        )
        for route in routes
        if _route_is_assignable(route, threshold=threshold)
    }


async def _record_route_dispositions(
    db: AsyncSession,
    *,
    project_id: str,
    routes: list[MemoirBookSourceRouteResponse],
) -> None:
    route_by_utterance = {route.utterance_id: route for route in routes}
    if not route_by_utterance:
        return
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceUtterance.id.in_(list(route_by_utterance)),
        )
    )
    for utterance in result.scalars().all():
        apply_source_route_disposition(utterance, route_by_utterance[utterance.id])
        db.add(utterance)
    await db.flush()


async def _record_assignment_events(
    db: AsyncSession,
    *,
    project_id: str,
    routes: list[MemoirBookSourceRouteResponse],
    plan: MemoirBookPlanResponse,
    threshold: float,
    source_lineage_by_utterance: dict[str, dict[str, object]] | None = None,
) -> list[MemoirAutomationEventResponse]:
    routes_by_chapter: dict[str, list[MemoirBookSourceRouteResponse]] = {}
    for route in routes:
        if not _route_is_assignable(route, threshold=threshold) or not route.target_chapter_id:
            continue
        routes_by_chapter.setdefault(route.target_chapter_id, []).append(route)

    titles = _chapter_titles(plan)
    responses: list[MemoirAutomationEventResponse] = []
    for chapter_id, chapter_routes in routes_by_chapter.items():
        utterance_ids = [route.utterance_id for route in chapter_routes]
        confidence_values = [route.confidence or 0 for route in chapter_routes]
        confidence = round(sum(confidence_values) / len(confidence_values), 3)
        title = titles.get(chapter_id) or chapter_routes[0].target_chapter_title or "未命名章节"
        event = await automation_events.record_or_update_automation_event(
            db,
            project_id=project_id,
            event_type=MemoirAutomationEventType.SOURCE_ASSIGNED,
            status=MemoirAutomationEventStatus.COMPLETED,
            chapter_id=chapter_id,
            utterance_ids=utterance_ids,
            summary_text=f"{len(utterance_ids)} 条讲述已自动整理进「{title}」。",
            event_signature=f"source_assigned:{chapter_id}:{','.join(sorted(utterance_ids))}",
            confidence=confidence,
            metadata_json={
                "source": "auto_assignment",
                "confidence_threshold": threshold,
                "routes": [
                    _route_metadata(route, source_lineage_by_utterance=source_lineage_by_utterance)
                    for route in chapter_routes
                ],
            },
        )
        responses.append(automation_events.event_response(event))
    await db.commit()
    return responses


def _held_event_summary(route: MemoirBookSourceRouteResponse) -> str:
    title = route.target_chapter_title or route.suggested_title or "待确认章节"
    if route.action == "suggest_new_chapter":
        return f"1 条讲述可能是新话题，建议确认是否新建「{title}」。"
    if route.action == "assign_to_chapter":
        return f"1 条讲述可能属于「{title}」，但判断把握还不够，需要确认。"
    return "1 条讲述暂未自动收入章节，需要确认归属。"


def _held_event_metadata(
    route: MemoirBookSourceRouteResponse,
    *,
    threshold: float,
    source_lineage_by_utterance: dict[str, dict[str, object]] | None,
) -> dict[str, object]:
    return {
        "source": "auto_assignment",
        "confidence_threshold": threshold,
        "route": _route_metadata(route, source_lineage_by_utterance=source_lineage_by_utterance),
    }


async def _pending_held_events_by_utterance(
    db: AsyncSession,
    *,
    project_id: str,
) -> dict[str, MemoirAutomationEvent]:
    result = await db.execute(
        select(MemoirAutomationEvent)
        .where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
            MemoirAutomationEvent.status == MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        )
        .order_by(MemoirAutomationEvent.created_at.desc(), MemoirAutomationEvent.id.desc())
    )
    events_by_utterance: dict[str, MemoirAutomationEvent] = {}
    for event in result.scalars().all():
        for utterance_id in event.utterance_ids_json or []:
            if isinstance(utterance_id, str) and utterance_id.strip():
                events_by_utterance.setdefault(utterance_id.strip(), event)
    return events_by_utterance


async def _record_held_events(
    db: AsyncSession,
    *,
    project_id: str,
    routes: list[MemoirBookSourceRouteResponse],
    threshold: float,
    source_lineage_by_utterance: dict[str, dict[str, object]] | None,
) -> list[MemoirAutomationEventResponse]:
    responses: list[MemoirAutomationEventResponse] = []
    pending_events = await _pending_held_events_by_utterance(db, project_id=project_id)
    for route in routes:
        if _route_is_assignable(route, threshold=threshold):
            continue
        if route.action == "ignore":
            continue
        summary = _held_event_summary(route)
        metadata = _held_event_metadata(
            route,
            threshold=threshold,
            source_lineage_by_utterance=source_lineage_by_utterance,
        )
        event = pending_events.get(route.utterance_id)
        if event:
            event.chapter_id = route.target_chapter_id
            event.summary_text = summary
            event.confidence = route.confidence
            event.metadata_json = metadata
            db.add(event)
            await db.flush()
        else:
            event = await automation_events.record_automation_event(
                db,
                project_id=project_id,
                event_type=MemoirAutomationEventType.SOURCE_HELD,
                status=MemoirAutomationEventStatus.NEEDS_REVIEW,
                chapter_id=route.target_chapter_id,
                utterance_ids=[route.utterance_id],
                summary_text=summary,
                confidence=route.confidence,
                metadata_json=metadata,
            )
            pending_events[route.utterance_id] = event
        responses.append(automation_events.event_response(event))
    await db.commit()
    return responses


async def run_source_auto_assignment_for_utterances(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    utterance_ids: list[str],
    ai_model: str | None = None,
    confidence_threshold: float = 0.53,
    record_held_events: bool = True,
    source_lineage_by_utterance: dict[str, dict[str, object]] | None = None,
) -> MemoirBookSourceAutoAssignmentRunResponse:
    routes: list[MemoirBookSourceRouteResponse] = []
    for utterance_id in dict.fromkeys(item.strip() for item in utterance_ids if item.strip()):
        routes.append(
            await source_router.route_book_source(
                db,
                user_id=user_id,
                project_id=project_id,
                utterance_id=utterance_id,
                ai_model=ai_model,
            )
        )

    await _record_route_dispositions(db, project_id=project_id, routes=routes)

    assignment_inputs = _group_assignment_inputs(routes, threshold=confidence_threshold)
    assigned_ids = {
        utterance_id
        for assignment in assignment_inputs
        for utterance_id in assignment.utterance_ids
    }
    if assignment_inputs:
        plan = await book_source_workflow.assign_book_sources_to_chapters(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookSourceAssignmentRequest(
                ai_model=ai_model,
                assignments=assignment_inputs,
            ),
            assigned_by=MemoirChapterSourceAssignedBy.AI,
            assignment_reason="auto_assignment",
            assignment_metadata_by_utterance=_assignment_link_metadata(routes, threshold=confidence_threshold),
        )
        for utterance_id in sorted(assigned_ids):
            await source_cards.schedule_source_card_generation_job(
                db,
                project_id=project_id,
                utterance_id=utterance_id,
                ai_model=ai_model,
                reason="source_auto_assigned",
            )
        events = await _record_assignment_events(
            db,
            project_id=project_id,
            routes=routes,
            plan=plan,
            threshold=confidence_threshold,
            source_lineage_by_utterance=source_lineage_by_utterance,
        )
    else:
        plan = await book_plans.get_book_plan(db, user_id=user_id, project_id=project_id)
        events = []

    if record_held_events:
        events.extend(
            await _record_held_events(
                db,
                project_id=project_id,
                routes=routes,
                threshold=confidence_threshold,
                source_lineage_by_utterance=source_lineage_by_utterance,
            )
        )

    if routes:
        await db.commit()

    items = [
        _auto_assignment_item(
            route,
            assigned=route.utterance_id in assigned_ids,
        )
        for route in routes
    ]
    return MemoirBookSourceAutoAssignmentRunResponse(
        project_id=project_id,
        confidence_threshold=confidence_threshold,
        evaluated_source_count=len(routes),
        assigned_source_count=len(assigned_ids),
        skipped_source_count=len(routes) - len(assigned_ids),
        items=items,
        events=events,
        plan=plan,
    )


async def run_source_routing_job(db: AsyncSession, job: MemoirEditingJob) -> EditingJobResult:
    project = await db.get(Project, job.project_id)
    if not project or not project.user_id:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="project unavailable")
    payload = job.payload_json or {}
    raw_utterance_ids = payload.get("utterance_ids", [])
    if not isinstance(raw_utterance_ids, list):
        raw_utterance_ids = []
    utterance_ids = [
        item
        for item in raw_utterance_ids
        if isinstance(item, str) and item.strip()
    ]
    if not utterance_ids:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="empty utterance_ids")
    guard = await validate_source_routing_transcript_guard(
        db,
        project_id=job.project_id,
        payload_json=payload,
        utterance_ids=utterance_ids,
    )
    if not guard.ok:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                "stale_utterance_ids": list(guard.stale_utterance_ids),
            },
            error_message=guard.error_message,
        )
    raw_threshold = payload.get("confidence_threshold")
    try:
        confidence_threshold = float(raw_threshold if raw_threshold is not None else 0.53)
    except (TypeError, ValueError):
        confidence_threshold = 0.53
    try:
        source_lineage = source_routing_lineage_by_utterance(
            payload,
            utterance_ids=utterance_ids,
            editing_job_id=job.id,
        )
        response = await run_source_auto_assignment_for_utterances(
            db,
            user_id=project.user_id,
            project_id=job.project_id,
            utterance_ids=utterance_ids,
            ai_model=payload.get("ai_model") if isinstance(payload.get("ai_model"), str) else None,
            confidence_threshold=confidence_threshold,
            source_lineage_by_utterance=source_lineage,
        )
    except HTTPException as exc:
        if exc.status_code in {400, 404}:
            return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message=str(exc.detail))
        raise
    if response.assigned_source_count:
        await book_quality_audit.schedule_project_quality_audit(
            db,
            project_id=job.project_id,
            reason="source_routing_completed",
        )
    return EditingJobResult(
        result_json={
            "evaluated_source_count": response.evaluated_source_count,
            "assigned_source_count": response.assigned_source_count,
            "skipped_source_count": response.skipped_source_count,
            "event_ids": [event.id for event in response.events],
        },
    )


async def run_source_auto_assignment(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceAutoAssignmentRunRequest,
) -> MemoirBookSourceAutoAssignmentRunResponse:
    coverage = await book_source_workflow.get_book_source_coverage(db, user_id=user_id, project_id=project_id)
    return await run_source_auto_assignment_for_utterances(
        db,
        user_id=user_id,
        project_id=project_id,
        utterance_ids=[source.utterance_id for source in coverage.uncovered_sources[: req.max_sources]],
        ai_model=req.ai_model,
        confidence_threshold=req.confidence_threshold,
        record_held_events=False,
    )
