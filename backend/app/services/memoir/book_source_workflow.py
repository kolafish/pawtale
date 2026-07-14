from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChapterSourceAssignedBy,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_book import (
    MemoirBookPlanResponse,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceCoverageResponse,
    MemoirBookSourceMoveRequest,
    MemoirBookSourceRemoveRequest,
)
from app.services.memoir import automation_event_resolution as _automation_event_resolution
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_auto_revision import (
    remember_project_ai_model as _remember_project_ai_model,
    schedule_chapter_auto_revision as _schedule_chapter_auto_revision,
    schedule_chapter_auto_revisions as _schedule_chapter_auto_revisions,
)
from app.services.memoir.book_plan_sources import (
    NEW_SOURCES_ASSIGNED_STATUS_REASON,
    SOURCES_CHANGED_STATUS_REASON,
    as_dict as _as_dict,
    draft_status_metadata as _draft_status_metadata,
    load_plan_with_chapters as _load_plan,
    merge_metadata as _merge_metadata,
    pending_source_ids as _pending_source_ids,
    plan_response as _plan_response,
    removed_source_ids as _removed_source_ids,
    set_source_assignment_metadata as _set_source_assignment_metadata,
    set_source_change_metadata as _set_source_change_metadata,
    source_coverage_item as _source_coverage_item,
    source_ref_utterance_id as _source_ref_utterance_id,
    source_segments as _source_segments,
)
from app.services.memoir.book_source_links import (
    covered_utterance_ids as _covered_utterance_ids,
    included_chapters_by_utterance as _included_chapters_by_utterance,
    plan_source_ref_dicts_by_chapter as _plan_source_ref_dicts_by_chapter,
)
from app.services.memoir.book_structure_commands import chapter_by_id as _chapter_by_id
from app.services.memoir.chapter_source_links import (
    ChapterSourceAssignmentMetadata,
    sync_plan_chapter_source_links as _sync_plan_chapter_source_links,
)
from app.services.memoir.utterance_editing_pipeline import schedule_source_routing_for_ready_utterance
from app.services.memoir.source_disposition import CONTEXT_SOURCE_ROUTE_ACTION


PLAN_CONFIRMED_SOURCE_ROUTING_SOURCE = "book_plan_confirmed"


async def _mark_chapter_drafts_sources_changed(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: set[str],
) -> None:
    if not chapter_ids:
        return
    drafts = (
        await db.execute(
            select(MemoirBookChapterDraft).where(
                MemoirBookChapterDraft.project_id == project_id,
                MemoirBookChapterDraft.chapter_id.in_(chapter_ids),
            )
        )
    ).scalars().all()
    for draft in drafts:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = _draft_status_metadata(draft.metadata_json, SOURCES_CHANGED_STATUS_REASON)
        db.add(draft)


async def get_book_source_coverage(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> MemoirBookSourceCoverageResponse:
    sources = await _source_segments(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    sources = [
        source
        for source in sources
        if source.source_route_action != CONTEXT_SOURCE_ROUTE_ACTION
    ]
    plan = await _load_plan(db, project_id)
    included_by_utterance = await _included_chapters_by_utterance(db, plan)
    covered_ids = set(included_by_utterance)
    uncovered_sources = [
        source for source in sources
        if source.utterance_id not in covered_ids
    ]
    covered_source_count = len(sources) - len(uncovered_sources)
    return MemoirBookSourceCoverageResponse(
        project_id=project_id,
        plan_id=plan.id if plan else None,
        total_source_count=len(sources),
        covered_source_count=covered_source_count,
        uncovered_source_count=len(uncovered_sources),
        sources=[
            _source_coverage_item(source, included_by_utterance.get(source.utterance_id, []))
            for source in sources
        ],
        uncovered_sources=[_source_coverage_item(source) for source in uncovered_sources],
    )


async def schedule_uncovered_source_routing_after_plan_confirmation(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> int:
    coverage = await get_book_source_coverage(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    utterance_ids = [source.utterance_id for source in coverage.uncovered_sources]
    if not utterance_ids:
        return 0

    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceUtterance.id.in_(utterance_ids),
        )
        .options(selectinload(MemoirVoiceUtterance.chat_message))
    )
    utterances_by_id = {utterance.id: utterance for utterance in result.scalars().all()}
    scheduled_count = 0
    for utterance_id in utterance_ids:
        utterance = utterances_by_id.get(utterance_id)
        if not utterance:
            continue
        job = await schedule_source_routing_for_ready_utterance(
            db,
            project_id=project_id,
            utterance=utterance,
            source=PLAN_CONFIRMED_SOURCE_ROUTING_SOURCE,
        )
        if job:
            scheduled_count += 1
    if scheduled_count:
        await db.commit()
    return scheduled_count


async def assign_book_sources_to_chapters(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceAssignmentRequest,
    assigned_by: MemoirChapterSourceAssignedBy | str = MemoirChapterSourceAssignedBy.USER,
    assignment_reason: str = "manual_source_assignment",
    assignment_metadata_by_utterance: dict[str, ChapterSourceAssignmentMetadata] | None = None,
    allow_admin: bool = False,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    _remember_project_ai_model(project, req.ai_model)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    sources = await _source_segments(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    source_by_utterance = {source.utterance_id: source for source in sources}
    covered_ids = await _covered_utterance_ids(db, plan)
    chapter_by_id = {chapter.id: chapter for chapter in plan.chapters}
    refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)

    affected_chapter_ids: set[str] = set()
    assigned_utterance_ids: list[str] = []
    for assignment in req.assignments:
        chapter = chapter_by_id.get(assignment.chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="章节不存在")

        refs = list(refs_by_chapter.get(chapter.id, []))
        pending_ids = _pending_source_ids(chapter.metadata_json)
        for utterance_id in assignment.utterance_ids:
            source = source_by_utterance.get(utterance_id)
            if not source:
                raise HTTPException(status_code=400, detail="讲述记录不属于当前回忆录")
            if utterance_id in covered_ids:
                raise HTTPException(status_code=400, detail="讲述记录已收录到章节")
            refs.append(_as_dict(source.ref()))
            covered_ids.add(utterance_id)
            pending_ids.add(utterance_id)
            affected_chapter_ids.add(chapter.id)
            assigned_utterance_ids.append(utterance_id)

        refs_by_chapter[chapter.id] = refs
        chapter.metadata_json = _set_source_assignment_metadata(chapter.metadata_json, pending_ids)
        db.add(chapter)

    if assigned_utterance_ids:
        await db.flush()
        await _sync_plan_chapter_source_links(
            db,
            plan=plan,
            source_refs_by_chapter=refs_by_chapter,
            assigned_by=assigned_by,
            assignment_reason=assignment_reason,
            assignment_metadata_by_utterance=assignment_metadata_by_utterance,
        )
        await _automation_event_resolution.resolve_assigned_source_review_events(
            db,
            project_id=project_id,
            utterance_ids=assigned_utterance_ids,
        )
    if affected_chapter_ids:
        drafts = (
            await db.execute(
                select(MemoirBookChapterDraft).where(
                    MemoirBookChapterDraft.project_id == project_id,
                    MemoirBookChapterDraft.chapter_id.in_(affected_chapter_ids),
                )
            )
        ).scalars().all()
        for draft in drafts:
            if draft.status == MemoirBookChapterDraftStatus.CONFIRMED.value:
                draft.status = MemoirBookChapterDraftStatus.DRAFT.value
                draft.metadata_json = _draft_status_metadata(
                    draft.metadata_json,
                    NEW_SOURCES_ASSIGNED_STATUS_REASON,
                )
                db.add(draft)
        await _schedule_chapter_auto_revisions(
            db,
            project_id=project_id,
            chapter_ids=affected_chapter_ids,
            ai_model=req.ai_model,
        )

    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


async def move_book_source_between_chapters(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceMoveRequest,
    allow_admin: bool = False,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    _remember_project_ai_model(project, req.ai_model)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    source_chapter = _chapter_by_id(plan, req.source_chapter_id)
    target_chapter = _chapter_by_id(plan, req.target_chapter_id)
    refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    source_refs = list(refs_by_chapter.get(source_chapter.id, []))
    target_refs = list(refs_by_chapter.get(target_chapter.id, []))

    if len(source_refs) <= 1:
        raise HTTPException(status_code=400, detail="原章节至少需要保留一条来源")
    if any(_source_ref_utterance_id(ref) == req.utterance_id for ref in target_refs):
        raise HTTPException(status_code=400, detail="讲述记录已收录到目标章节")

    moving_ref: dict[str, object] | None = None
    kept_source_refs: list[dict[str, object]] = []
    for ref in source_refs:
        if _source_ref_utterance_id(ref) == req.utterance_id:
            moving_ref = dict(ref)
            continue
        kept_source_refs.append(ref)
    if moving_ref is None:
        raise HTTPException(status_code=404, detail="原章节中没有找到这条来源")
    if not kept_source_refs:
        raise HTTPException(status_code=400, detail="原章节至少需要保留一条来源")

    source_pending_ids = _pending_source_ids(source_chapter.metadata_json)
    source_pending_ids.discard(req.utterance_id)
    source_removed_ids = _removed_source_ids(source_chapter.metadata_json)
    source_removed_ids.add(req.utterance_id)
    target_pending_ids = _pending_source_ids(target_chapter.metadata_json)
    target_pending_ids.add(req.utterance_id)
    target_removed_ids = _removed_source_ids(target_chapter.metadata_json)
    target_removed_ids.discard(req.utterance_id)

    refs_by_chapter[source_chapter.id] = kept_source_refs
    source_chapter.metadata_json = _set_source_change_metadata(
        source_chapter.metadata_json,
        added_ids=source_pending_ids,
        removed_ids=source_removed_ids,
    )
    refs_by_chapter[target_chapter.id] = [*target_refs, moving_ref]
    target_chapter.metadata_json = _set_source_change_metadata(
        target_chapter.metadata_json,
        added_ids=target_pending_ids,
        removed_ids=target_removed_ids,
    )

    now = datetime.now(UTC).isoformat()
    plan.metadata_json = _merge_metadata(
        plan.metadata_json,
        {
            "source_moved_at": now,
            "structure_updated_at": now,
            "structure_updated_by": "user",
        },
    )
    db.add(source_chapter)
    db.add(target_chapter)
    db.add(plan)
    await _mark_chapter_drafts_sources_changed(
        db,
        project_id=project_id,
        chapter_ids={source_chapter.id, target_chapter.id},
    )
    await _schedule_chapter_auto_revision(
        db,
        project_id=project_id,
        chapter_id=target_chapter.id,
        ai_model=req.ai_model,
    )
    await db.flush()
    await _sync_plan_chapter_source_links(
        db,
        plan=plan,
        source_refs_by_chapter=refs_by_chapter,
        assigned_by=MemoirChapterSourceAssignedBy.USER,
        assignment_reason="manual_source_move",
    )
    await _automation_event_resolution.resolve_assigned_source_review_events(
        db,
        project_id=project_id,
        utterance_ids=[req.utterance_id],
    )
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


async def remove_book_sources_from_chapter(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceRemoveRequest,
    allow_admin: bool = False,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    _remember_project_ai_model(project, req.ai_model)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    chapter = _chapter_by_id(plan, req.chapter_id)
    refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    refs = list(refs_by_chapter.get(chapter.id, []))
    requested_ids = set(req.utterance_ids)
    removed_ids: set[str] = set()
    kept_refs: list[dict[str, object]] = []
    for ref in refs:
        utterance_id = _source_ref_utterance_id(ref)
        if utterance_id in requested_ids:
            removed_ids.add(utterance_id)
            continue
        kept_refs.append(ref)

    if requested_ids - removed_ids:
        raise HTTPException(status_code=404, detail="章节中没有找到要移出的来源")
    if not kept_refs:
        raise HTTPException(status_code=400, detail="章节至少需要保留一条来源")

    pending_ids = _pending_source_ids(chapter.metadata_json)
    pending_ids.difference_update(removed_ids)
    removed_source_ids = _removed_source_ids(chapter.metadata_json)
    removed_source_ids.update(removed_ids)
    chapter.metadata_json = _set_source_change_metadata(
        chapter.metadata_json,
        added_ids=pending_ids,
        removed_ids=removed_source_ids,
    )
    refs_by_chapter[chapter.id] = kept_refs
    now = datetime.now(UTC).isoformat()
    plan.metadata_json = _merge_metadata(
        plan.metadata_json,
        {
            "source_removed_at": now,
            "structure_updated_at": now,
            "structure_updated_by": "user",
        },
    )
    db.add(chapter)
    db.add(plan)
    await _mark_chapter_drafts_sources_changed(
        db,
        project_id=project_id,
        chapter_ids={chapter.id},
    )
    await db.flush()
    await _sync_plan_chapter_source_links(
        db,
        plan=plan,
        source_refs_by_chapter=refs_by_chapter,
        assigned_by=MemoirChapterSourceAssignedBy.USER,
        assignment_reason="manual_source_remove",
    )
    await _schedule_chapter_auto_revision(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_model=req.ai_model,
    )
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)
