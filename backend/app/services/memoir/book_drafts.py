from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.schemas.memoir_book import (
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookChapterDraftResponse,
    MemoirBookChapterDraftVersionResponse,
)
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
from app.services.memoir.book_plan_sources import (
    DRAFT_STATUS_REASON_KEY,
    REMOVED_SOURCE_UTTERANCE_IDS_KEY,
    metadata_without_keys,
    source_change_metadata_keys,
    source_change_counts,
)
from app.services.memoir.book_source_links import (
    chapter_source_change_state,
    mark_chapter_sources_merged,
)
from app.services.memoir.narrative.draft_assembly import (
    assert_chapter_draft_snapshot_current,
)
from app.services.memoir.voice_tts_types import TTSClient


CHAPTER_TTS_CACHE_KEY = "chapter_tts"


def _version_response(
    version: MemoirBookChapterDraftVersion,
) -> MemoirBookChapterDraftVersionResponse:
    return MemoirBookChapterDraftVersionResponse(
        id=version.id,
        draft_id=version.draft_id,
        project_id=version.project_id,
        chapter_id=version.chapter_id,
        version=version.version,
        content=version.content,
        created_by=MemoirCreatedBy(version.created_by),
        model=version.model,
        prompt_snapshot=version.prompt_snapshot,
        feedback=version.feedback,
        block_snapshot_json=[
            item for item in (version.block_snapshot_json or []) if isinstance(item, dict)
        ],
        created_at=version.created_at,
    )


def _draft_response(
    draft: MemoirBookChapterDraft,
    *,
    chapter_metadata_json: dict | None = None,
    pending_source_count: int | None = None,
    removed_source_count: int | None = None,
) -> MemoirBookChapterDraftResponse:
    versions = [
        _version_response(version)
        for version in sorted(
            draft.versions,
            key=lambda item: (item.version, item.created_at, item.id),
        )
    ]
    if pending_source_count is None or removed_source_count is None:
        pending_source_count, removed_source_count = source_change_counts(
            chapter_metadata_json
        )
    return MemoirBookChapterDraftResponse(
        id=draft.id,
        project_id=draft.project_id,
        chapter_id=draft.chapter_id,
        status=MemoirBookChapterDraftStatus(draft.status),
        metadata_json=draft.metadata_json or {},
        pending_source_count=pending_source_count,
        removed_source_count=removed_source_count,
        latest_version=versions[-1] if versions else None,
        versions=versions,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


async def _load_chapter(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    allow_admin: bool = False,
) -> MemoirBookPlanChapter:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .join(MemoirBookPlan, MemoirBookPlanChapter.plan_id == MemoirBookPlan.id)
        .where(
            MemoirBookPlan.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
        .options(
            selectinload(MemoirBookPlanChapter.plan),
            selectinload(MemoirBookPlanChapter.draft).selectinload(
                MemoirBookChapterDraft.versions
            ),
        )
        .execution_options(populate_existing=True)
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


def _require_confirmed_plan(chapter: MemoirBookPlanChapter) -> None:
    if chapter.plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")


async def _load_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookChapterDraft | None:
    return await db.scalar(
        select(MemoirBookChapterDraft)
        .where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
        .options(selectinload(MemoirBookChapterDraft.versions))
        .execution_options(populate_existing=True)
    )


async def get_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    allow_admin: bool = False,
) -> MemoirBookChapterDraftResponse:
    chapter = await _load_chapter(
        db,
        user_id=user_id,
        project_id=project_id,
        chapter_id=chapter_id,
        allow_admin=allow_admin,
    )
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="章节稿不存在")
    changes = await chapter_source_change_state(db, chapter=chapter)
    return _draft_response(
        draft,
        chapter_metadata_json=chapter.metadata_json,
        pending_source_count=changes.pending_source_count,
        removed_source_count=changes.removed_source_count,
    )


async def confirm_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    allow_admin: bool = False,
) -> MemoirBookChapterDraftResponse:
    chapter = await _load_chapter(
        db,
        user_id=user_id,
        project_id=project_id,
        chapter_id=chapter_id,
        allow_admin=allow_admin,
    )
    _require_confirmed_plan(chapter)
    changes = await chapter_source_change_state(db, chapter=chapter)
    if changes.pending_source_ids or changes.removed_source_ids:
        raise HTTPException(status_code=400, detail="请先在章节工作台重新组装正文")
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if draft is None or not draft.versions:
        raise HTTPException(status_code=400, detail="章节稿为空，无法确认")
    latest = max(
        draft.versions,
        key=lambda item: (item.version, item.created_at, item.id),
    )
    if not [
        item for item in (latest.block_snapshot_json or []) if isinstance(item, dict)
    ]:
        raise HTTPException(status_code=400, detail="请先在章节工作台组装结构化正文")
    try:
        await assert_chapter_draft_snapshot_current(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            block_snapshot=latest.block_snapshot_json,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="正文块在组装后发生变化，请重新组装本章",
        ) from exc
    draft.status = MemoirBookChapterDraftStatus.CONFIRMED.value
    draft.metadata_json = metadata_without_keys(
        draft.metadata_json,
        {DRAFT_STATUS_REASON_KEY, REMOVED_SOURCE_UTTERANCE_IDS_KEY},
    )
    chapter.metadata_json = metadata_without_keys(
        chapter.metadata_json,
        source_change_metadata_keys(),
    )
    db.add(chapter)
    await mark_chapter_sources_merged(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        draft_version_id=latest.id,
        include_removed=True,
    )
    await db.commit()
    refreshed = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if refreshed is None:
        raise HTTPException(status_code=500, detail="章节稿确认失败")
    return _draft_response(
        refreshed,
        chapter_metadata_json=chapter.metadata_json,
        pending_source_count=0,
        removed_source_count=0,
    )


async def synthesize_chapter_draft_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookAudioRequest,
    tts_client: TTSClient | None = None,
    allow_admin: bool = False,
) -> MemoirBookAudioResponse:
    chapter = await _load_chapter(
        db,
        user_id=user_id,
        project_id=project_id,
        chapter_id=chapter_id,
        allow_admin=allow_admin,
    )
    _require_confirmed_plan(chapter)
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if draft is None or not draft.versions:
        raise HTTPException(status_code=400, detail="章节稿为空，无法朗读")
    latest = max(
        draft.versions,
        key=lambda item: (item.version, item.created_at, item.id),
    )
    text = latest.content.strip()
    text_hash = source_text_hash(text)
    speed_ratio = effective_tts_speed_ratio(req.speed_ratio)
    cached = cached_audio_response(
        draft.metadata_json or {},
        cache_key=CHAPTER_TTS_CACHE_KEY,
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
        draft.metadata_json = {
            **(draft.metadata_json or {}),
            CHAPTER_TTS_CACHE_KEY: {
                **audio_cache_payload(
                    synthesis,
                    voice=req.voice,
                    speed_ratio=speed_ratio,
                ),
                "draft_version_id": latest.id,
                "chapter_id": chapter.id,
            },
        }
        db.add(draft)
        await db.commit()
    except Exception:
        await db.rollback()
        discard_staged_audio_files(synthesis)
        raise
    return audio_response(synthesis)
