from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirBookPlanChapterStatus, MemoirBookPlanStatus
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_voice import MemoirAutomationEventUtteranceDetailResponse
from app.services.memoir.automation_event_metadata import (
    ROUTES_KEY,
    ROUTE_KEY,
    UTTERANCE_ID_KEY,
    _clean_utterance_ids,
    _event_utterance_ids,
)
from app.services.memoir.book_source_links import (
    plan_source_ref_dicts_by_chapter,
    source_ref_utterance_id,
)


@dataclass(frozen=True)
class AssignedSourceChapter:
    chapter_id: str
    chapter_title: str


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


async def assigned_source_chapters(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: set[str] | None = None,
    confirmed_plan_only: bool = False,
) -> dict[str, AssignedSourceChapter]:
    statement = select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id)
    if confirmed_plan_only:
        statement = statement.where(MemoirBookPlan.status == MemoirBookPlanStatus.CONFIRMED.value)
    result = await db.execute(statement)
    assigned: dict[str, AssignedSourceChapter] = {}
    for plan in result.scalars().all():
        chapters = await load_plan_chapters(db, plan)
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


async def load_plan_chapters(db: AsyncSession, plan: MemoirBookPlan) -> list[MemoirBookPlanChapter]:
    return (
        await db.execute(
            select(MemoirBookPlanChapter)
            .where(
                MemoirBookPlanChapter.project_id == plan.project_id,
                MemoirBookPlanChapter.plan_id == plan.id,
                MemoirBookPlanChapter.status == MemoirBookPlanChapterStatus.ACTIVE.value,
            )
            .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
        )
    ).scalars().all()


async def utterance_details_by_event(
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
        chapters = await load_plan_chapters(db, plan)
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
