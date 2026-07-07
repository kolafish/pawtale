from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChapterSourceAssignedBy,
    MemoirCreatedBy,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.schemas.memoir_book import (
    MemoirBookChapterSourceRef,
    MemoirBookChapterTitleGenerateRequest,
    MemoirBookChapterTitleUpdateRequest,
    MemoirBookPlanChapterInput,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanResponse,
    MemoirBookPlanStructureCommandRequest,
    MemoirBookPlanUpdateRequest,
)
from app.services.ai_types import AITextClient
from app.services.memoir import automation_events as _automation_events
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_auto_revision import (
    schedule_chapter_auto_revisions as _schedule_chapter_auto_revisions,
)
from app.services.memoir.book_plan_ai import (
    ChapterTitleSourceDigest,
    generate_book_plan_from_sources as _generate_book_plan_from_sources,
    generate_chapter_title_from_sources as _generate_chapter_title_from_sources,
)
from app.services.memoir.book_plan_sources import (
    SourceSegment,
    SOURCES_CHANGED_STATUS_REASON,
    as_dict as _as_dict,
    draft_status_metadata as _draft_status_metadata,
    load_plan_with_chapters as _load_plan,
    merge_metadata as _merge_metadata,
    plan_response as _plan_response,
    renumber_chapters as _renumber_chapters,
    set_source_change_metadata as _set_source_change_metadata,
    set_temporary_chapter_orders as _set_temporary_chapter_orders,
    source_ref_utterance_ids as _source_ref_utterance_ids,
    source_segments as _source_segments,
    sorted_chapters as _sorted_plan_chapters,
    text_or_empty as _text_or_empty,
)
from app.services.memoir.book_source_links import (
    covered_utterance_ids as _covered_utterance_ids,
    plan_source_ref_dicts_by_chapter as _plan_source_ref_dicts_by_chapter,
)
from app.services.memoir.book_source_workflow import (
    schedule_uncovered_source_routing_after_plan_confirmation as _schedule_uncovered_source_routing_after_plan_confirmation,
)
from app.services.memoir.book_structure_commands import (
    apply_book_structure_command as _apply_book_structure_command,
    chapter_by_id as _chapter_by_id,
    command_chapter_id as _require_command_chapter_id,
    source_utterance_id as _require_source_utterance_id,
)
from app.services.memoir.chapter_source_links import (
    sync_plan_chapter_source_links as _sync_plan_chapter_source_links,
)


@dataclass(frozen=True)
class ValidatedChapterUpdate:
    id: str | None
    title: str
    source_refs: list[MemoirBookChapterSourceRef]


def _link_assigned_by_for_created_by(created_by: MemoirCreatedBy) -> MemoirChapterSourceAssignedBy:
    if created_by == MemoirCreatedBy.AI:
        return MemoirChapterSourceAssignedBy.AI
    if created_by == MemoirCreatedBy.USER:
        return MemoirChapterSourceAssignedBy.USER
    return MemoirChapterSourceAssignedBy.SYSTEM


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


async def _handle_chapter_source_changes(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: set[str],
    ai_model: str | None = None,
) -> None:
    if not chapter_ids:
        return
    await _mark_chapter_drafts_sources_changed(
        db,
        project_id=project_id,
        chapter_ids=chapter_ids,
    )
    await _schedule_chapter_auto_revisions(
        db,
        project_id=project_id,
        chapter_ids=chapter_ids,
        ai_model=ai_model,
    )


async def _delete_plan_chapters(db: AsyncSession, plan: MemoirBookPlan) -> None:
    chapters = list(plan.chapters)
    if not chapters:
        return
    chapter_ids = [chapter.id for chapter in chapters]
    drafts = (
        await db.execute(
            select(MemoirBookChapterDraft)
            .where(MemoirBookChapterDraft.chapter_id.in_(chapter_ids))
            .options(selectinload(MemoirBookChapterDraft.versions))
        )
    ).scalars().all()
    for draft in drafts:
        for version in list(draft.versions):
            await db.delete(version)
        await db.delete(draft)
    for chapter in chapters:
        await db.delete(chapter)
    await db.flush()
    plan.chapters = []


async def _replace_plan(
    db: AsyncSession,
    *,
    project_id: str,
    title: str,
    chapters: list[MemoirBookPlanChapterInput],
    status: MemoirBookPlanStatus,
    created_by: MemoirCreatedBy,
    model: str | None,
    prompt_snapshot: str,
    metadata_json: dict[str, object] | None = None,
) -> MemoirBookPlanResponse:
    plan = await _load_plan(db, project_id)
    if not plan:
        plan = MemoirBookPlan(project_id=project_id)
        db.add(plan)
        await db.flush()
    else:
        await _delete_plan_chapters(db, plan)

    plan.title = title[:200]
    plan.status = status.value
    plan.created_by = created_by.value
    plan.model = model
    plan.prompt_snapshot = prompt_snapshot
    plan.metadata_json = metadata_json or {}

    pending_source_refs: list[tuple[MemoirBookPlanChapter, list[dict[str, object]]]] = []
    for index, chapter in enumerate(chapters):
        plan_chapter = MemoirBookPlanChapter(
            plan_id=plan.id,
            project_id=project_id,
            title=chapter.title.strip()[:200],
            sort_order=index,
        )
        db.add(plan_chapter)
        pending_source_refs.append((plan_chapter, [_as_dict(ref) for ref in chapter.source_refs]))
    await db.flush()
    source_refs_by_chapter = {chapter.id: refs for chapter, refs in pending_source_refs}
    await _sync_plan_chapter_source_links(
        db,
        plan=plan,
        source_refs_by_chapter=source_refs_by_chapter,
        assigned_by=_link_assigned_by_for_created_by(created_by),
        assignment_reason="replace_book_plan",
    )
    if created_by == MemoirCreatedBy.USER:
        await _automation_events.resolve_assigned_source_review_events(db, project_id=project_id)
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


async def generate_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanGenerateRequest,
    ai_client: AITextClient | None = None,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id)
    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    if not sources:
        raise HTTPException(status_code=400, detail="暂无讲述内容，无法规划章节")
    generated = await _generate_book_plan_from_sources(
        project_title=project.title,
        sources=sources,
        max_chapters=req.max_chapters,
        ai_model=req.ai_model,
        ai_client=ai_client,
    )
    return await _replace_plan(
        db,
        project_id=project_id,
        title=generated.title,
        chapters=generated.chapters,
        status=MemoirBookPlanStatus.DRAFT,
        created_by=MemoirCreatedBy.AI,
        model=generated.model,
        prompt_snapshot=generated.prompt,
        metadata_json={
            "source_utterance_count": len(sources),
            "prompt_version": "memoir_book_plan_v1",
        },
    )


def _validated_chapters(
    req: MemoirBookPlanConfirmRequest,
    sources: list[SourceSegment],
) -> list[MemoirBookPlanChapterInput]:
    source_by_utterance = {source.utterance_id: source for source in sources}
    chapters: list[MemoirBookPlanChapterInput] = []
    for chapter in req.chapters:
        refs: list[MemoirBookChapterSourceRef] = []
        seen_utterances: set[str] = set()
        for ref in chapter.source_refs:
            source = source_by_utterance.get(ref.utterance_id)
            if not source:
                raise HTTPException(status_code=400, detail="章节来源不属于当前回忆录")
            if source.utterance_id in seen_utterances:
                continue
            refs.append(source.ref(ref.quote))
            seen_utterances.add(source.utterance_id)
        if not refs:
            raise HTTPException(status_code=400, detail="每个章节都需要至少一个来源")
        chapters.append(
            MemoirBookPlanChapterInput(
                title=chapter.title,
                source_refs=refs,
            )
        )
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")
    return chapters


def _validated_chapter_updates(
    req: MemoirBookPlanUpdateRequest,
    sources: list[SourceSegment],
) -> list[ValidatedChapterUpdate]:
    source_by_utterance = {source.utterance_id: source for source in sources}
    chapters: list[ValidatedChapterUpdate] = []
    seen_plan_utterances: set[str] = set()
    for chapter in req.chapters:
        refs: list[MemoirBookChapterSourceRef] = []
        seen_chapter_utterances: set[str] = set()
        for ref in chapter.source_refs:
            source = source_by_utterance.get(ref.utterance_id)
            if not source:
                raise HTTPException(status_code=400, detail="章节来源不属于当前回忆录")
            if source.utterance_id in seen_chapter_utterances:
                continue
            if source.utterance_id in seen_plan_utterances:
                raise HTTPException(status_code=400, detail="同一条讲述记录不能重复归入多个章节")
            refs.append(source.ref(ref.quote))
            seen_chapter_utterances.add(source.utterance_id)
            seen_plan_utterances.add(source.utterance_id)
        if not refs:
            raise HTTPException(status_code=400, detail="每个章节都需要至少一个来源")
        chapters.append(
            ValidatedChapterUpdate(
                id=chapter.id,
                title=chapter.title,
                source_refs=refs,
            )
        )
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")
    return chapters


def _touch_plan_structure(plan: MemoirBookPlan, *, operation: str, now: str) -> None:
    plan.created_by = MemoirCreatedBy.USER.value
    plan.metadata_json = _merge_metadata(
        plan.metadata_json,
        {
            "structure_updated_at": now,
            "structure_updated_by": "user",
            "structure_operation": operation,
        },
    )


def _chapter_title_source_digests(
    chapter: MemoirBookPlanChapter,
    sources: list[SourceSegment],
    *,
    source_refs: list[dict[str, object]] | None = None,
) -> list[ChapterTitleSourceDigest]:
    source_by_utterance = {source.utterance_id: source for source in sources}
    digests: list[ChapterTitleSourceDigest] = []
    for ref in source_refs or []:
        if not isinstance(ref, dict):
            continue
        utterance_id = ref.get("utterance_id")
        source = source_by_utterance.get(utterance_id) if isinstance(utterance_id, str) else None
        text = source.text if source else str(ref.get("quote") or "")
        text = _text_or_empty(text)
        if text:
            quote = _text_or_empty(str(ref.get("quote") or "")) or text
            digests.append(
                ChapterTitleSourceDigest(
                    source_number=len(digests) + 1,
                    utterance_id=utterance_id if isinstance(utterance_id, str) else "",
                    created_at=source.created_at.isoformat() if source else "",
                    summary=text,
                    representative_quote=quote,
                )
            )
    return digests


async def _save_chapter_title(
    db: AsyncSession,
    *,
    project_id: str,
    plan: MemoirBookPlan,
    chapter: MemoirBookPlanChapter,
    title: str,
    updated_by: MemoirCreatedBy,
    model: str | None = None,
    prompt_snapshot: str = "",
    structure_operation: str | None = None,
) -> MemoirBookPlanResponse:
    now = datetime.now(UTC).isoformat()
    previous_title = chapter.title
    chapter.title = title.strip()[:200]
    chapter.metadata_json = _merge_metadata(
        chapter.metadata_json,
        {
            "title_updated_at": now,
            "title_updated_by": updated_by.value,
            "previous_title": previous_title,
            **({"title_model": model} if model else {}),
            **({"title_prompt_snapshot": prompt_snapshot} if prompt_snapshot else {}),
        },
    )
    plan_metadata_patch = {
        "chapter_title_updated_at": now,
        "chapter_title_updated_by": updated_by.value,
    }
    if structure_operation:
        plan.created_by = MemoirCreatedBy.USER.value
        plan_metadata_patch.update(
            {
                "structure_updated_at": now,
                "structure_updated_by": "user",
                "structure_operation": structure_operation,
            }
        )
    plan.metadata_json = _merge_metadata(plan.metadata_json, plan_metadata_patch)
    db.add(chapter)
    db.add(plan)
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节标题保存失败")
    return await _plan_response(db, refreshed)


async def confirm_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanConfirmRequest,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id)
    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    chapters = _validated_chapters(req, sources)
    response = await _replace_plan(
        db,
        project_id=project_id,
        title=req.title or f"{project.title}章节规划",
        chapters=chapters,
        status=MemoirBookPlanStatus.CONFIRMED,
        created_by=MemoirCreatedBy.USER,
        model=None,
        prompt_snapshot="",
        metadata_json={
            "source_utterance_count": len(sources),
            "confirmed_by": "user",
        },
    )
    await _schedule_uncovered_source_routing_after_plan_confirmation(
        db,
        user_id=user_id,
        project_id=project_id,
    )
    return response


async def update_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanUpdateRequest,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    chapter_updates = _validated_chapter_updates(req, sources)
    existing_by_id = {chapter.id: chapter for chapter in plan.chapters}
    current_refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    retained_ids = {chapter.id for chapter in chapter_updates if chapter.id}
    unknown_ids = retained_ids - set(existing_by_id)
    if unknown_ids:
        raise HTTPException(status_code=404, detail="章节不存在")

    for index, chapter in enumerate(plan.chapters):
        chapter.sort_order = 10000 + index
        db.add(chapter)
    await db.flush()

    changed_chapter_ids: set[str] = set()
    newly_assigned_utterance_ids: set[str] = set()
    pending_source_refs: list[tuple[MemoirBookPlanChapter, list[dict[str, object]]]] = []
    now = datetime.now(UTC).isoformat()
    for index, chapter_update in enumerate(chapter_updates):
        if chapter_update.id:
            chapter = existing_by_id[chapter_update.id]
            old_ids = set(_source_ref_utterance_ids(current_refs_by_chapter.get(chapter.id, [])))
            new_ids = set(_source_ref_utterance_ids(chapter_update.source_refs))
            added_ids = new_ids - old_ids
            removed_ids = old_ids - new_ids
            chapter.title = chapter_update.title.strip()[:200]
            chapter.sort_order = index
            pending_source_refs.append((chapter, [_as_dict(ref) for ref in chapter_update.source_refs]))
            if added_ids or removed_ids:
                chapter.metadata_json = _set_source_change_metadata(
                    chapter.metadata_json,
                    added_ids=added_ids,
                    removed_ids=removed_ids,
                )
                changed_chapter_ids.add(chapter.id)
                newly_assigned_utterance_ids.update(added_ids)
            db.add(chapter)
        else:
            new_source_ids = set(_source_ref_utterance_ids(chapter_update.source_refs))
            newly_assigned_utterance_ids.update(new_source_ids)
            chapter = MemoirBookPlanChapter(
                plan_id=plan.id,
                project_id=project_id,
                title=chapter_update.title.strip()[:200],
                sort_order=index,
                metadata_json={"created_from_plan_update_at": now},
            )
            db.add(chapter)
            pending_source_refs.append((chapter, [_as_dict(ref) for ref in chapter_update.source_refs]))

    removed_chapters = [
        chapter for chapter_id, chapter in existing_by_id.items()
        if chapter_id not in retained_ids
    ]
    for chapter in removed_chapters:
        if chapter in plan.chapters:
            plan.chapters.remove(chapter)
        else:
            await db.delete(chapter)

    if changed_chapter_ids or newly_assigned_utterance_ids or removed_chapters:
        await db.flush()
        refs_by_chapter = {chapter.id: refs for chapter, refs in pending_source_refs}
        await _sync_plan_chapter_source_links(
            db,
            plan=plan,
            source_refs_by_chapter=refs_by_chapter,
            assigned_by=MemoirChapterSourceAssignedBy.USER,
            assignment_reason="book_plan_update",
        )
        await _automation_events.resolve_assigned_source_review_events(
            db,
            project_id=project_id,
            utterance_ids=sorted(newly_assigned_utterance_ids) if newly_assigned_utterance_ids else None,
        )
    await _handle_chapter_source_changes(
        db,
        project_id=project_id,
        chapter_ids=changed_chapter_ids,
    )

    plan.title = (req.title or plan.title).strip()[:200]
    plan.created_by = MemoirCreatedBy.USER.value
    plan.metadata_json = _merge_metadata(
        plan.metadata_json,
        {
            "structure_updated_at": now,
            "structure_updated_by": "user",
        },
    )
    db.add(plan)
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


async def apply_book_plan_structure_command(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanStructureCommandRequest,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    if req.type == "rename_chapter":
        chapter_id = _require_command_chapter_id(req)
        if not req.title:
            raise HTTPException(status_code=400, detail="章节标题不能为空")
        return await _save_chapter_title(
            db,
            project_id=project_id,
            plan=plan,
            chapter=_chapter_by_id(plan, chapter_id),
            title=req.title,
            updated_by=MemoirCreatedBy.USER,
            structure_operation=req.type,
        )

    chapters = _sorted_plan_chapters(plan)
    _set_temporary_chapter_orders(chapters)
    for chapter in chapters:
        db.add(chapter)
    await db.flush()

    now = datetime.now(UTC).isoformat()
    source: SourceSegment | None = None
    refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    if req.type == "create_chapter_from_source":
        utterance_id = _require_source_utterance_id(req)
        if utterance_id in await _covered_utterance_ids(db, plan):
            raise HTTPException(status_code=400, detail="讲述记录已收录到章节")
        sources = await _source_segments(db, user_id=user_id, project_id=project_id)
        source = next((item for item in sources if item.utterance_id == utterance_id), None)
        if not source:
            raise HTTPException(status_code=400, detail="讲述记录不属于当前回忆录")

    mutation = _apply_book_structure_command(
        plan=plan,
        chapters=chapters,
        req=req,
        project_id=project_id,
        now=now,
        source_refs_by_chapter=refs_by_chapter,
        source=source,
    )
    chapters = mutation.chapters
    for chapter in mutation.deleted_chapters:
        if chapter in plan.chapters:
            plan.chapters.remove(chapter)
        else:
            await db.delete(chapter)

    _renumber_chapters(chapters)
    for chapter in chapters:
        db.add(chapter)
    if mutation.changed_chapter_ids or source or mutation.deleted_chapters:
        await db.flush()
        await _sync_plan_chapter_source_links(
            db,
            plan=plan,
            source_refs_by_chapter=mutation.source_refs_by_chapter,
            assigned_by=MemoirChapterSourceAssignedBy.USER,
            assignment_reason=req.type,
        )
        await _automation_events.resolve_assigned_source_review_events(
            db,
            project_id=project_id,
            utterance_ids=[source.utterance_id] if source else None,
        )
    if mutation.changed_chapter_ids:
        await _handle_chapter_source_changes(
            db,
            project_id=project_id,
            chapter_ids=mutation.changed_chapter_ids,
        )
    _touch_plan_structure(plan, operation=req.type, now=now)
    db.add(plan)
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


async def update_book_chapter_title(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterTitleUpdateRequest,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    chapter = _chapter_by_id(plan, chapter_id)
    return await _save_chapter_title(
        db,
        project_id=project_id,
        plan=plan,
        chapter=chapter,
        title=req.title,
        updated_by=MemoirCreatedBy.USER,
    )


async def generate_book_chapter_title(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterTitleGenerateRequest,
    ai_client: AITextClient | None = None,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    chapter = _chapter_by_id(plan, chapter_id)
    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    generated = await _generate_chapter_title_from_sources(
        sources=_chapter_title_source_digests(
            chapter,
            sources,
            source_refs=refs_by_chapter.get(chapter.id, []),
        ),
        ai_model=req.ai_model,
        ai_client=ai_client,
    )
    return await _save_chapter_title(
        db,
        project_id=project_id,
        plan=plan,
        chapter=chapter,
        title=generated.title,
        updated_by=MemoirCreatedBy.AI,
        model=generated.model,
        prompt_snapshot=generated.prompt,
    )
