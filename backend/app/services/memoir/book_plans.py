from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirBookChapterDraftStatus, MemoirBookPlanStatus, MemoirCreatedBy
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookChapterDraft,
)
from app.schemas.memoir_book import (
    MemoirBookChapterTitleGenerateRequest,
    MemoirBookChapterTitleUpdateRequest,
    MemoirBookChapterSourceRef,
    MemoirBookPlanChapterInput,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanUpdateRequest,
    MemoirBookPlanResponse,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceCoverageResponse,
    MemoirBookSourceMoveRequest,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_auto_revision import (
    remember_project_ai_model as _remember_project_ai_model,
    schedule_chapter_auto_revision as _schedule_chapter_auto_revision,
    schedule_chapter_auto_revisions as _schedule_chapter_auto_revisions,
)
from app.services.memoir.book_plan_ai import (
    AI_BOOK_PLAN_UNAVAILABLE_MESSAGE,
    CHAPTER_TITLE_SYSTEM_PROMPT,
    ChapterTitleSourceDigest,
    SYSTEM_PROMPT,
    build_chapter_title_prompt as _build_chapter_title_prompt,
    build_plan_prompt as _build_plan_prompt,
    chapters_from_payload as _chapters_from_payload,
    clean_generated_chapter_title as _clean_generated_chapter_title,
)
from app.services.memoir.book_plan_sources import (
    SourceSegment,
    as_dict as _as_dict,
    covered_utterance_ids as _covered_utterance_ids,
    included_chapters_by_utterance as _included_chapters_by_utterance,
    merge_metadata as _merge_metadata,
    plan_response as _plan_response,
    set_source_change_metadata as _set_source_change_metadata,
    source_coverage_item as _source_coverage_item,
    source_ids_from_metadata as _source_ids_from_metadata,
    source_ref_utterance_id as _source_ref_utterance_id,
    source_ref_utterance_ids as _source_ref_utterance_ids,
    source_segments as _source_segments,
    text_or_empty as _text_or_empty,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidatedChapterUpdate:
    id: str | None
    title: str
    source_refs: list[MemoirBookChapterSourceRef]


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
        draft.metadata_json = _merge_metadata(
            draft.metadata_json,
            {"status_reason": "sources_changed"},
        )
        db.add(draft)


async def _load_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(selectinload(MemoirBookPlan.chapters))
        .execution_options(populate_existing=True)
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

    for index, chapter in enumerate(chapters):
        db.add(
            MemoirBookPlanChapter(
                plan_id=plan.id,
                project_id=project_id,
                title=chapter.title.strip()[:200],
                sort_order=index,
                source_refs_json=[_as_dict(ref) for ref in chapter.source_refs],
            )
        )
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


async def get_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return await _plan_response(db, plan)


async def get_book_source_coverage(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookSourceCoverageResponse:
    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    plan = await _load_plan(db, project_id)
    included_by_utterance = _included_chapters_by_utterance(plan)
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


async def assign_book_sources_to_chapters(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceAssignmentRequest,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id)
    _remember_project_ai_model(project, req.ai_model)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    source_by_utterance = {source.utterance_id: source for source in sources}
    covered_ids = _covered_utterance_ids(plan)
    chapter_by_id = {chapter.id: chapter for chapter in plan.chapters}

    affected_chapter_ids: set[str] = set()
    for assignment in req.assignments:
        chapter = chapter_by_id.get(assignment.chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="章节不存在")

        refs = list(chapter.source_refs_json or [])
        pending_ids = set(
            item for item in (chapter.metadata_json or {}).get("pending_source_utterance_ids", [])
            if isinstance(item, str)
        )
        for utterance_id in assignment.utterance_ids:
            source = source_by_utterance.get(utterance_id)
            if not source:
                raise HTTPException(status_code=400, detail="转写不属于当前回忆录")
            if utterance_id in covered_ids:
                raise HTTPException(status_code=400, detail="转写已收录到章节")
            refs.append(_as_dict(source.ref()))
            covered_ids.add(utterance_id)
            pending_ids.add(utterance_id)
            affected_chapter_ids.add(chapter.id)

        chapter.source_refs_json = refs
        chapter.metadata_json = _merge_metadata(
            chapter.metadata_json,
            {
                "pending_source_utterance_ids": sorted(pending_ids),
                "source_assignment_updated_at": datetime.now(UTC).isoformat(),
            },
        )
        db.add(chapter)

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
                draft.metadata_json = _merge_metadata(
                    draft.metadata_json,
                    {"status_reason": "new_sources_assigned"},
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
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id)
    _remember_project_ai_model(project, req.ai_model)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    source_chapter = _chapter_by_id(plan, req.source_chapter_id)
    target_chapter = _chapter_by_id(plan, req.target_chapter_id)
    source_refs = list(source_chapter.source_refs_json or [])
    target_refs = list(target_chapter.source_refs_json or [])

    if len(source_refs) <= 1:
        raise HTTPException(status_code=400, detail="原章节至少需要保留一条来源")
    if any(_source_ref_utterance_id(ref) == req.utterance_id for ref in target_refs):
        raise HTTPException(status_code=400, detail="转写已收录到目标章节")

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

    source_pending_ids = _source_ids_from_metadata(source_chapter.metadata_json, "pending_source_utterance_ids")
    source_pending_ids.discard(req.utterance_id)
    source_removed_ids = _source_ids_from_metadata(source_chapter.metadata_json, "removed_source_utterance_ids")
    source_removed_ids.add(req.utterance_id)
    target_pending_ids = _source_ids_from_metadata(target_chapter.metadata_json, "pending_source_utterance_ids")
    target_pending_ids.add(req.utterance_id)
    target_removed_ids = _source_ids_from_metadata(target_chapter.metadata_json, "removed_source_utterance_ids")
    target_removed_ids.discard(req.utterance_id)

    source_chapter.source_refs_json = kept_source_refs
    source_chapter.metadata_json = _set_source_change_metadata(
        source_chapter.metadata_json,
        added_ids=source_pending_ids,
        removed_ids=source_removed_ids,
    )
    target_chapter.source_refs_json = [*target_refs, moving_ref]
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
        raise HTTPException(status_code=400, detail="暂无转写内容，无法规划章节")
    prompt = _build_plan_prompt(project.title, sources, req.max_chapters)
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(req.ai_model)
        content = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.2,
            max_tokens=2400,
            timeout=120.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir book plan failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir book plan failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc

    payload = extract_json_payload(str(content))
    title, chapters = _chapters_from_payload(payload, sources=sources, max_chapters=req.max_chapters)
    return await _replace_plan(
        db,
        project_id=project_id,
        title=title,
        chapters=chapters,
        status=MemoirBookPlanStatus.DRAFT,
        created_by=MemoirCreatedBy.AI,
        model=getattr(client, "model_name", req.ai_model),
        prompt_snapshot=prompt,
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
                raise HTTPException(status_code=400, detail="同一条转写不能重复归入多个章节")
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


def _chapter_by_id(plan: MemoirBookPlan, chapter_id: str) -> MemoirBookPlanChapter:
    for chapter in plan.chapters:
        if chapter.id == chapter_id:
            return chapter
    raise HTTPException(status_code=404, detail="章节不存在")


def _chapter_title_source_digests(
    chapter: MemoirBookPlanChapter,
    sources: list[SourceSegment],
) -> list[ChapterTitleSourceDigest]:
    source_by_utterance = {source.utterance_id: source for source in sources}
    digests: list[ChapterTitleSourceDigest] = []
    for ref in chapter.source_refs_json or []:
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
    plan.metadata_json = _merge_metadata(
        plan.metadata_json,
        {
            "chapter_title_updated_at": now,
            "chapter_title_updated_by": updated_by.value,
        },
    )
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
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    chapter_updates = _validated_chapter_updates(req, sources)
    existing_by_id = {chapter.id: chapter for chapter in plan.chapters}
    retained_ids = {chapter.id for chapter in chapter_updates if chapter.id}
    unknown_ids = retained_ids - set(existing_by_id)
    if unknown_ids:
        raise HTTPException(status_code=404, detail="章节不存在")

    for index, chapter in enumerate(plan.chapters):
        chapter.sort_order = 10000 + index
        db.add(chapter)
    await db.flush()

    changed_chapter_ids: set[str] = set()
    now = datetime.now(UTC).isoformat()
    for index, chapter_update in enumerate(chapter_updates):
        if chapter_update.id:
            chapter = existing_by_id[chapter_update.id]
            old_ids = set(_source_ref_utterance_ids(chapter.source_refs_json or []))
            new_ids = set(_source_ref_utterance_ids(chapter_update.source_refs))
            added_ids = new_ids - old_ids
            removed_ids = old_ids - new_ids
            chapter.title = chapter_update.title.strip()[:200]
            chapter.sort_order = index
            chapter.source_refs_json = [_as_dict(ref) for ref in chapter_update.source_refs]
            if added_ids or removed_ids:
                chapter.metadata_json = _set_source_change_metadata(
                    chapter.metadata_json,
                    added_ids=added_ids,
                    removed_ids=removed_ids,
                )
                changed_chapter_ids.add(chapter.id)
            db.add(chapter)
        else:
            db.add(
                MemoirBookPlanChapter(
                    plan_id=plan.id,
                    project_id=project_id,
                    title=chapter_update.title.strip()[:200],
                    sort_order=index,
                    source_refs_json=[_as_dict(ref) for ref in chapter_update.source_refs],
                    metadata_json={"created_from_plan_update_at": now},
                )
            )

    removed_chapters = [
        chapter for chapter_id, chapter in existing_by_id.items()
        if chapter_id not in retained_ids
    ]
    for chapter in removed_chapters:
        if chapter in plan.chapters:
            plan.chapters.remove(chapter)
        else:
            await db.delete(chapter)

    await _mark_chapter_drafts_sources_changed(
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
    prompt = _build_chapter_title_prompt(_chapter_title_source_digests(chapter, sources))

    client = ai_client
    try:
        if client is None:
            client = get_ai_service(req.ai_model)
        content = await client._call_llm(
            CHAPTER_TITLE_SYSTEM_PROMPT,
            prompt,
            temperature=0.25,
            max_tokens=160,
            timeout=60.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir chapter title failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir chapter title failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc

    title = _clean_generated_chapter_title(str(content))
    return await _save_chapter_title(
        db,
        project_id=project_id,
        plan=plan,
        chapter=chapter,
        title=title,
        updated_by=MemoirCreatedBy.AI,
        model=getattr(client, "model_name", req.ai_model),
        prompt_snapshot=prompt,
    )
