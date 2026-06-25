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
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookChapterTitleGenerateRequest,
    MemoirBookChapterTitleUpdateRequest,
    MemoirBookChapterSourceRef,
    MemoirBookPlanChapterInput,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanStructureCommandRequest,
    MemoirBookPlanUpdateRequest,
    MemoirBookPlanResponse,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceCoverageResponse,
    MemoirBookSourceMoveRequest,
)
from app.services.ai_types import AITextClient
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio import (
    audio_cache_payload,
    audio_response,
    cached_audio_response,
    discard_staged_audio_files,
    effective_tts_speed_ratio,
    source_text_hash,
    synthesize_text_audio,
)
from app.services.memoir.book_auto_revision import (
    remember_project_ai_model as _remember_project_ai_model,
    schedule_chapter_auto_revision as _schedule_chapter_auto_revision,
    schedule_chapter_auto_revisions as _schedule_chapter_auto_revisions,
)
from app.services.memoir.book_plan_ai import (
    ChapterTitleSourceDigest,
    generate_book_plan_from_sources as _generate_book_plan_from_sources,
    generate_chapter_title_from_sources as _generate_chapter_title_from_sources,
)
from app.services.memoir.book_plan_sources import (
    NEW_SOURCES_ASSIGNED_STATUS_REASON,
    SourceSegment,
    SOURCES_CHANGED_STATUS_REASON,
    as_dict as _as_dict,
    covered_utterance_ids as _covered_utterance_ids,
    draft_status_metadata as _draft_status_metadata,
    included_chapters_by_utterance as _included_chapters_by_utterance,
    merge_metadata as _merge_metadata,
    plan_response as _plan_response,
    pending_source_ids as _pending_source_ids,
    renumber_chapters as _renumber_chapters,
    removed_source_ids as _removed_source_ids,
    set_source_assignment_metadata as _set_source_assignment_metadata,
    set_source_change_metadata as _set_source_change_metadata,
    set_temporary_chapter_orders as _set_temporary_chapter_orders,
    source_coverage_item as _source_coverage_item,
    source_ref_utterance_id as _source_ref_utterance_id,
    source_ref_utterance_ids as _source_ref_utterance_ids,
    source_segments as _source_segments,
    sorted_chapters as _sorted_plan_chapters,
    text_or_empty as _text_or_empty,
)
from app.services.memoir.book_structure_commands import (
    apply_book_structure_command as _apply_book_structure_command,
    chapter_by_id as _chapter_by_id,
    command_chapter_id as _require_command_chapter_id,
    source_utterance_id as _require_source_utterance_id,
)
from app.services.memoir.voice_tts import TTSClient


logger = logging.getLogger(__name__)
BOOK_OUTLINE_TTS_CACHE_KEY = "outline_tts_audio"


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
        draft.metadata_json = _draft_status_metadata(draft.metadata_json, SOURCES_CHANGED_STATUS_REASON)
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


async def synthesize_book_outline_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookAudioRequest,
    tts_client: TTSClient | None = None,
) -> MemoirBookAudioResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    text = build_book_outline_audio_text(plan)
    text_hash = source_text_hash(text)
    speed_ratio = effective_tts_speed_ratio(req.speed_ratio)
    cached = cached_audio_response(
        plan.metadata_json or {},
        cache_key=BOOK_OUTLINE_TTS_CACHE_KEY,
        text_hash=text_hash,
        voice=req.voice,
        speed_ratio=speed_ratio,
        force_regenerate=req.force_regenerate,
    )
    if cached:
        return cached

    synthesis = await synthesize_text_audio(
        db,
        user_id=user_id,
        text=text,
        voice=req.voice,
        speed_ratio=req.speed_ratio,
        tts_client=tts_client,
    )
    try:
        plan.metadata_json = {
            **(plan.metadata_json or {}),
            BOOK_OUTLINE_TTS_CACHE_KEY: audio_cache_payload(synthesis, voice=req.voice, speed_ratio=speed_ratio),
        }
        db.add(plan)
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio_files(synthesis.staged_audio)
        raise
    return audio_response(synthesis)


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

        chapter.source_refs_json = refs
        chapter.metadata_json = _set_source_assignment_metadata(chapter.metadata_json, pending_ids)
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


def build_book_outline_audio_text(plan: MemoirBookPlan) -> str:
    chapters = sorted(plan.chapters, key=lambda chapter: chapter.sort_order)
    title = (plan.title or "回忆录章节规划").strip() or "回忆录章节规划"
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划为空，无法朗读")

    lines = [f"《{title}》现在有 {len(chapters)} 章。"]
    for index, chapter in enumerate(chapters, start=1):
        chapter_title = (chapter.title or "未命名章节").strip() or "未命名章节"
        source_count = len(chapter.source_refs_json or [])
        lines.append(f"第 {index} 章，{chapter_title}。本章收录 {source_count} 条语音素材。")
    lines.append("你可以继续调整章节顺序、合并章节，或者说出想修改的地方。")
    return "\n".join(lines)


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
    if req.type == "create_chapter_from_source":
        utterance_id = _require_source_utterance_id(req)
        if utterance_id in _covered_utterance_ids(plan):
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
    if mutation.changed_chapter_ids:
        await _mark_chapter_drafts_sources_changed(
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
    generated = await _generate_chapter_title_from_sources(
        sources=_chapter_title_source_digests(chapter, sources),
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
