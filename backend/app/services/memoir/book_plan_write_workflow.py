from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCompletionIssueScope,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirSectionMaterialAssignedBy,
    MemoirSectionStatus,
)
from app.domain.memoir.limits import MAX_MEMOIR_BOOK_PLAN_CHAPTERS
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookImagePlacement,
    MemoirBookOutput,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirImageAsset,
    MemoirSection,
    MemoirSectionMaterial,
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
from app.services.memoir.access import load_memoir_project
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
    set_temporary_chapter_orders as _set_temporary_chapter_orders,
    source_ref_utterance_ids as _source_ref_utterance_ids,
    source_segments as _source_segments,
    sorted_chapters as _sorted_plan_chapters,
    text_or_empty as _text_or_empty,
)
from app.services.memoir.material_roles import interviewer_lines
from app.services.memoir.project_ai_preferences import select_project_ai_model
from app.services.memoir.book_source_links import (
    plan_source_ref_dicts_by_chapter as _plan_source_ref_dicts_by_chapter,
)
from app.services.memoir.book_structure_commands import (
    apply_book_structure_command as _apply_book_structure_command,
    chapter_by_id as _chapter_by_id,
    command_chapter_id as _require_command_chapter_id,
)
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.sections import sync_materials_to_chapters
from app.services.memoir.section_digests import mark_section_digests_stale
from app.services.memoir import fact_decisions
from app.services.memoir.completion_issues import lock_project_chapters


@dataclass(frozen=True)
class ValidatedChapterUpdate:
    id: str | None
    title: str
    include_in_book: bool
    source_refs: list[MemoirBookChapterSourceRef]


async def _detach_chapter_operational_references(
    db: AsyncSession,
    chapters: list[MemoirBookPlanChapter],
) -> None:
    chapter_ids = {chapter.id for chapter in chapters}
    if not chapter_ids:
        return
    issues = (await db.scalars(select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id.in_({chapter.project_id for chapter in chapters}),
    ).order_by(MemoirCompletionIssue.id).with_for_update()
        .execution_options(populate_existing=True))).all()
    for issue in issues:
        if not fact_decisions.is_confirmed_fact(issue):
            continue
        # A chapter can disappear after its stories move elsewhere. Preserve
        # the user's answer and its source anchors before the FK cascades.
        await fact_decisions.bind_sources(db, issue)
        if issue.chapter_id in chapter_ids:
            issue.related_chapter_ids_json = [issue.chapter_id]
            issue.chapter_id = None
            issue.scope = MemoirCompletionIssueScope.BOOK.value
        db.add(issue)
    chapter_titles = {chapter.id: chapter.title for chapter in chapters}
    detached_at = datetime.now(UTC).isoformat()

    events = list(
        (
            await db.scalars(
                select(MemoirAutomationEvent).where(
                    MemoirAutomationEvent.chapter_id.in_(chapter_ids)
                )
            )
        ).all()
    )
    for event in events:
        deleted_chapter_id = event.chapter_id
        event.chapter_id = None
        event.metadata_json = {
            **(event.metadata_json or {}),
            "deleted_chapter": {
                "id": deleted_chapter_id,
                "title": chapter_titles.get(deleted_chapter_id or "", ""),
                "detached_at": detached_at,
            },
        }
        db.add(event)

    jobs = list(
        (
            await db.scalars(
                select(MemoirEditingJob).where(
                    MemoirEditingJob.chapter_id.in_(chapter_ids)
                )
            )
        ).all()
    )
    for job in jobs:
        job.chapter_id = None
        if job.status in {
            MemoirEditingJobStatus.PENDING.value,
            MemoirEditingJobStatus.RUNNING.value,
        }:
            job.status = MemoirEditingJobStatus.SKIPPED.value
            job.locked_at = None
            job.lease_token = None
            job.lease_expires_at = None
            job.error_message = "章节已删除，任务已跳过"
        db.add(job)

    await db.execute(
        delete(MemoirBookImagePlacement).where(
            MemoirBookImagePlacement.chapter_id.in_(chapter_ids)
        )
    )
    await db.execute(
        update(MemoirImageAsset)
        .where(MemoirImageAsset.source_chapter_id.in_(chapter_ids))
        .values(source_chapter_id=None)
    )
    await db.flush()


def _material_assigned_by_for_created_by(
    created_by: MemoirCreatedBy,
) -> MemoirSectionMaterialAssignedBy:
    if created_by == MemoirCreatedBy.AI:
        return MemoirSectionMaterialAssignedBy.AI
    if created_by == MemoirCreatedBy.USER:
        return MemoirSectionMaterialAssignedBy.USER
    return MemoirSectionMaterialAssignedBy.SYSTEM


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
) -> None:
    if not chapter_ids:
        return
    await _mark_chapter_drafts_sources_changed(
        db,
        project_id=project_id,
        chapter_ids=chapter_ids,
    )


async def _delete_plan_chapters(db: AsyncSession, plan: MemoirBookPlan) -> None:
    chapters = list(plan.chapters)
    if not chapters:
        return
    await _detach_chapter_operational_references(db, chapters)
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
    before_commit: Callable[[], Awaitable[None]] | None = None,
) -> MemoirBookPlanResponse:
    if len(chapters) > MAX_MEMOIR_BOOK_PLAN_CHAPTERS:
        raise HTTPException(
            status_code=400,
            detail=f"章节数量不能超过 {MAX_MEMOIR_BOOK_PLAN_CHAPTERS} 章",
        )
    await lock_project_chapters(db, project_id=project_id)
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
            include_in_book=chapter.include_in_book,
        )
        db.add(plan_chapter)
        pending_source_refs.append((plan_chapter, [_as_dict(ref) for ref in chapter.source_refs]))
    await db.flush()
    source_refs_by_chapter = {chapter.id: refs for chapter, refs in pending_source_refs}
    await sync_materials_to_chapters(
        db,
        project_id=project_id,
        utterance_ids_by_chapter={
            chapter_id: _source_ref_utterance_ids(refs)
            for chapter_id, refs in source_refs_by_chapter.items()
        },
        assigned_by=_material_assigned_by_for_created_by(created_by),
        assignment_reason="replace_book_plan",
    )
    if before_commit is not None:
        await before_commit()
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
    allow_admin: bool = False,
    status: MemoirBookPlanStatus = MemoirBookPlanStatus.DRAFT,
    before_commit: Callable[[], Awaitable[None]] | None = None,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    sources = await _source_segments(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    if not sources:
        raise HTTPException(status_code=400, detail="暂无讲述内容，无法规划章节")
    project_title = project.title
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    lines = await interviewer_lines(db, (source.message_id for source in sources))
    await db.commit()
    generated = await _generate_book_plan_from_sources(
        project_title=project_title,
        sources=sources,
        interviewer_lines=lines,
        ai_model=selected_ai_model,
        ai_client=ai_client,
    )
    return await _replace_plan(
        db,
        project_id=project_id,
        title=generated.title,
        before_commit=before_commit,
        chapters=generated.chapters,
        status=status,
        created_by=MemoirCreatedBy.AI,
        model=generated.model,
        prompt_snapshot=generated.prompt,
        metadata_json={
            "source_utterance_count": len(sources),
            "prompt_version": "memoir_book_plan_v1",
            **(
                {"confirmed_by": MemoirCreatedBy.AI.value}
                if status == MemoirBookPlanStatus.CONFIRMED
                else {}
            ),
        },
    )


def _validated_chapters(
    req: MemoirBookPlanConfirmRequest,
    sources: list[SourceSegment],
) -> list[MemoirBookPlanChapterInput]:
    source_by_utterance = {source.utterance_id: source for source in sources}
    chapters: list[MemoirBookPlanChapterInput] = []
    seen_plan_utterances: set[str] = set()
    for chapter in req.chapters:
        refs: list[MemoirBookChapterSourceRef] = []
        seen_utterances: set[str] = set()
        for ref in chapter.source_refs:
            source = source_by_utterance.get(ref.utterance_id)
            if not source:
                raise HTTPException(status_code=400, detail="章节来源不属于当前回忆录")
            if source.utterance_id in seen_utterances:
                continue
            if source.utterance_id in seen_plan_utterances:
                raise HTTPException(status_code=400, detail="同一条讲述记录不能重复归入多个章节")
            refs.append(source.ref(ref.quote))
            seen_utterances.add(source.utterance_id)
            seen_plan_utterances.add(source.utterance_id)
        if not refs:
            raise HTTPException(status_code=400, detail="每个章节都需要至少一个来源")
        chapters.append(
            MemoirBookPlanChapterInput(
                title=chapter.title,
                include_in_book=chapter.include_in_book,
                source_refs=refs,
            )
        )
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")
    if not any(chapter.include_in_book for chapter in chapters):
        raise HTTPException(status_code=400, detail="成书时至少需要保留一个章节")
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
                include_in_book=chapter.include_in_book,
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


async def _set_chapter_book_inclusion(
    db: AsyncSession,
    *,
    project_id: str,
    plan: MemoirBookPlan,
    req: MemoirBookPlanStructureCommandRequest,
    commit: bool = True,
) -> MemoirBookPlanResponse:
    if req.include_in_book is None:
        raise HTTPException(status_code=400, detail="缺少章节成书收录状态")
    chapter = _chapter_by_id(plan, _require_command_chapter_id(req))
    if chapter.include_in_book == req.include_in_book:
        return await _plan_response(db, plan)
    if not req.include_in_book:
        included_count = sum(item.include_in_book for item in plan.chapters)
        if included_count <= 1:
            raise HTTPException(status_code=400, detail="成书时至少需要保留一个章节")

    chapter.include_in_book = req.include_in_book
    now = datetime.now(UTC).isoformat()
    _touch_plan_structure(
        plan,
        operation="set_chapter_book_inclusion",
        now=now,
    )
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    if output is not None:
        invalidate_book_output(output)
        db.add(output)
    db.add(chapter)
    db.add(plan)
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=project_id, source="structure")
    await (db.commit() if commit else db.flush())
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节收录状态保存失败")
    return await _plan_response(db, refreshed)


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
    commit: bool = True,
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
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=project_id, source="structure")
    await (db.commit() if commit else db.flush())
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
    allow_admin: bool = False,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    sources = await _source_segments(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    chapters = _validated_chapters(req, sources)
    return await _replace_plan(
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


async def update_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanUpdateRequest,
    allow_admin: bool = False,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    await lock_project_chapters(db, project_id=project_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    sources = await _source_segments(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    chapter_updates = _validated_chapter_updates(req, sources)
    existing_by_id = {chapter.id: chapter for chapter in plan.chapters}
    current_refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    retained_ids = {chapter.id for chapter in chapter_updates if chapter.id}
    unknown_ids = retained_ids - set(existing_by_id)
    if unknown_ids:
        raise HTTPException(status_code=404, detail="章节不存在")
    if not any(
        chapter.include_in_book if chapter.id is None else existing_by_id[chapter.id].include_in_book
        for chapter in chapter_updates
    ):
        raise HTTPException(status_code=400, detail="成书时至少需要保留一个章节")
    removed_chapters = [
        chapter
        for chapter_id, chapter in existing_by_id.items()
        if chapter_id not in retained_ids
    ]

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
                include_in_book=chapter_update.include_in_book,
                metadata_json={"created_from_plan_update_at": now},
            )
            db.add(chapter)
            pending_source_refs.append((chapter, [_as_dict(ref) for ref in chapter_update.source_refs]))

    await _detach_chapter_operational_references(db, removed_chapters)
    for chapter in removed_chapters:
        if chapter in plan.chapters:
            plan.chapters.remove(chapter)
        else:
            await db.delete(chapter)

    if changed_chapter_ids or newly_assigned_utterance_ids or removed_chapters:
        await db.flush()
        refs_by_chapter = {chapter.id: refs for chapter, refs in pending_source_refs}
        await sync_materials_to_chapters(
            db,
            project_id=project_id,
            utterance_ids_by_chapter={
                chapter_id: _source_ref_utterance_ids(refs)
                for chapter_id, refs in refs_by_chapter.items()
            },
            assigned_by=MemoirSectionMaterialAssignedBy.USER,
            assignment_reason="book_plan_update",
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
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=project_id, source="structure")
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


async def _selected_sections_for_chapter_split(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    section_ids: list[str],
) -> tuple[list[MemoirSection], list[MemoirSection], list[str]]:
    source_sections = list((await db.scalars(
        select(MemoirSection)
        .where(MemoirSection.project_id == project_id, MemoirSection.chapter_id == chapter_id)
        .order_by(MemoirSection.sort_order, MemoirSection.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).all())
    selected_id_set = set(section_ids)
    selected = [section for section in source_sections if section.id in selected_id_set]
    if len(selected) != len(section_ids) or any(
        section.status != MemoirSectionStatus.ACTIVE.value or not section.include_in_book
        for section in selected
    ):
        raise HTTPException(status_code=400, detail="要移动的小节不在原章节的当前收录内容中")

    links = list((await db.scalars(
        select(MemoirSectionMaterial)
        .where(MemoirSectionMaterial.project_id == project_id, MemoirSectionMaterial.section_id.in_(section_ids))
        .order_by(MemoirSectionMaterial.section_id, MemoirSectionMaterial.sort_order, MemoirSectionMaterial.id)
        .with_for_update()
    )).all())
    included_by_section: dict[str, list[str]] = {section_id: [] for section_id in section_ids}
    for link in links:
        if link.include_in_book:
            included_by_section[link.section_id].append(link.utterance_id)
    selected_source_ids = [
        utterance_id
        for section in selected
        for utterance_id in included_by_section[section.id]
    ]
    return source_sections, selected, selected_source_ids


async def _move_split_sections(
    db: AsyncSession,
    *,
    project_id: str,
    source_sections: list[MemoirSection],
    selected_sections: list[MemoirSection],
    new_chapter_id: str,
) -> None:
    selected_ids = {section.id for section in selected_sections}
    for index, section in enumerate(selected_sections):
        section.chapter_id = new_chapter_id
        section.sort_order = index
        section.version += 1
        db.add(section)
    await db.flush()
    for index, section in enumerate(section for section in source_sections if section.id not in selected_ids):
        if section.sort_order != index:
            section.sort_order = index
            section.version += 1
            db.add(section)
            await db.flush()
    await mark_section_digests_stale(
        db,
        project_id=project_id,
        section_ids=selected_ids,
    )


async def apply_book_plan_structure_command(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanStructureCommandRequest,
    allow_admin: bool = False,
    commit: bool = True,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    if req.section_ids and req.type != "split_chapter":
        raise HTTPException(status_code=400, detail="只有拆分章节可以选择小节")
    if req.section_ids and req.utterance_ids:
        raise HTTPException(status_code=400, detail="拆分章节不能同时选择小节和讲述")
    await lock_project_chapters(db, project_id=project_id)
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
            commit=commit,
        )

    if req.type == "set_chapter_book_inclusion":
        return await _set_chapter_book_inclusion(
            db,
            project_id=project_id,
            plan=plan,
            req=req,
            commit=commit,
        )

    chapters = _sorted_plan_chapters(plan)
    _set_temporary_chapter_orders(chapters)
    for chapter in chapters:
        db.add(chapter)
    await db.flush()

    now = datetime.now(UTC).isoformat()

    source_sections: list[MemoirSection] = []
    selected_sections: list[MemoirSection] = []
    if req.section_ids:
        chapter_id = _require_command_chapter_id(req)
        source_sections, selected_sections, selected_source_ids = await _selected_sections_for_chapter_split(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            section_ids=req.section_ids,
        )
    refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    if req.section_ids:
        source_ids = set(_source_ref_utterance_ids(refs_by_chapter.get(chapter_id, [])))
        if not selected_source_ids or not set(selected_source_ids) < source_ids:
            raise HTTPException(status_code=400, detail="拆分时新旧章节都至少需要保留 1 条收录讲述")
        req = req.model_copy(update={"utterance_ids": selected_source_ids})

    old_chapter_ids = {chapter.id for chapter in chapters}
    mutation = _apply_book_structure_command(
        plan=plan,
        chapters=chapters,
        req=req,
        project_id=project_id,
        now=now,
        source_refs_by_chapter=refs_by_chapter,
    )
    chapters = mutation.chapters
    await _detach_chapter_operational_references(db, mutation.deleted_chapters)
    for chapter in mutation.deleted_chapters:
        if chapter in plan.chapters:
            plan.chapters.remove(chapter)
        else:
            await db.delete(chapter)

    _renumber_chapters(chapters)
    for chapter in chapters:
        db.add(chapter)
    if selected_sections:
        new_chapter_id = next(chapter.id for chapter in chapters if chapter.id not in old_chapter_ids)
        await db.flush()
        await _move_split_sections(
            db,
            project_id=project_id,
            source_sections=source_sections,
            selected_sections=selected_sections,
            new_chapter_id=new_chapter_id,
        )
        output = await db.scalar(
            select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
        )
        if output is not None:
            invalidate_book_output(output)
            db.add(output)
    elif mutation.changed_chapter_ids or any(
        refs_by_chapter.get(chapter.id) for chapter in mutation.deleted_chapters
    ):
        # Removing an empty chapter needs no material reassignment. Rebuilding
        # every chapter's sources here would collapse unrelated section groups.
        await db.flush()
        await sync_materials_to_chapters(
            db,
            project_id=project_id,
            utterance_ids_by_chapter={
                chapter_id: _source_ref_utterance_ids(refs)
                for chapter_id, refs in mutation.source_refs_by_chapter.items()
            },
            assigned_by=MemoirSectionMaterialAssignedBy.USER,
            assignment_reason=req.type,
        )
    if mutation.changed_chapter_ids:
        await _handle_chapter_source_changes(
            db,
            project_id=project_id,
            chapter_ids=mutation.changed_chapter_ids,
        )
    _touch_plan_structure(plan, operation=req.type, now=now)
    db.add(plan)
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=project_id, source="structure")
    await (db.commit() if commit else db.flush())
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
    allow_admin: bool = False,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
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
    allow_admin: bool = False,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    chapter = _chapter_by_id(plan, chapter_id)
    sources = await _source_segments(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    await db.commit()
    generated = await _generate_chapter_title_from_sources(
        sources=_chapter_title_source_digests(
            chapter,
            sources,
            source_refs=refs_by_chapter.get(chapter.id, []),
        ),
        ai_model=selected_ai_model,
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
