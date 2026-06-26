from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirAutomationEventStatus, MemoirAutomationEventType
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
from app.services.memoir import book_plans
from app.services.memoir import source_router


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
        else:
            skip_reason = "not_existing_chapter_assignment"
    return MemoirBookSourceAutoAssignmentItemResponse(
        utterance_id=route.utterance_id,
        source_index=route.source_index,
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


async def _record_assignment_events(
    db: AsyncSession,
    *,
    project_id: str,
    routes: list[MemoirBookSourceRouteResponse],
    plan: MemoirBookPlanResponse,
    threshold: float,
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
        event = await automation_events.record_automation_event(
            db,
            project_id=project_id,
            event_type=MemoirAutomationEventType.SOURCE_ASSIGNED,
            status=MemoirAutomationEventStatus.COMPLETED,
            chapter_id=chapter_id,
            utterance_ids=utterance_ids,
            summary_text=f"{len(utterance_ids)} 条讲述已自动整理进「{title}」。",
            confidence=confidence,
            metadata_json={
                "source": "auto_assignment",
                "confidence_threshold": threshold,
                "routes": [
                    {
                        "utterance_id": route.utterance_id,
                        "source_index": route.source_index,
                        "confidence": route.confidence,
                        "reason": route.reason,
                    }
                    for route in chapter_routes
                ],
            },
        )
        responses.append(automation_events.event_response(event))
    await db.commit()
    return responses


async def run_source_auto_assignment(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceAutoAssignmentRunRequest,
) -> MemoirBookSourceAutoAssignmentRunResponse:
    coverage = await book_plans.get_book_source_coverage(db, user_id=user_id, project_id=project_id)
    routes: list[MemoirBookSourceRouteResponse] = []
    for source in coverage.uncovered_sources[: req.max_sources]:
        routes.append(
            await source_router.route_book_source(
                db,
                user_id=user_id,
                project_id=project_id,
                utterance_id=source.utterance_id,
            )
        )

    assignment_inputs = _group_assignment_inputs(routes, threshold=req.confidence_threshold)
    if assignment_inputs:
        plan = await book_plans.assign_book_sources_to_chapters(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookSourceAssignmentRequest(
                ai_model=req.ai_model,
                assignments=assignment_inputs,
            ),
        )
        events = await _record_assignment_events(
            db,
            project_id=project_id,
            routes=routes,
            plan=plan,
            threshold=req.confidence_threshold,
        )
    else:
        plan = await book_plans.get_book_plan(db, user_id=user_id, project_id=project_id)
        events = []

    assigned_ids = {
        utterance_id
        for assignment in assignment_inputs
        for utterance_id in assignment.utterance_ids
    }
    items = [
        _auto_assignment_item(
            route,
            assigned=route.utterance_id in assigned_ids,
        )
        for route in routes
    ]
    return MemoirBookSourceAutoAssignmentRunResponse(
        project_id=project_id,
        confidence_threshold=req.confidence_threshold,
        evaluated_source_count=len(routes),
        assigned_source_count=len(assigned_ids),
        skipped_source_count=len(routes) - len(assigned_ids),
        items=items,
        events=events,
        plan=plan,
    )
