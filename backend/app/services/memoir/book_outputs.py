from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.schemas.memoir_book import (
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookOutputGenerateRequest,
    MemoirBookOutputResponse,
    MemoirBookOutputVersionResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio import (
    audio_cache_payload,
    audio_response,
    cached_audio_response,
    discard_staged_audio_files,
    source_text_hash,
    synthesize_text_audio,
)
from app.services.memoir.voice_tts import TTSClient


BOOK_TTS_CACHE_KEY = "book_tts"
SNAPSHOT_COMPARE_KEYS = (
    "chapter_id",
    "chapter_title",
    "chapter_sort_order",
    "draft_id",
    "draft_version_id",
    "draft_version",
    "content_char_count",
)


def _latest_chapter_version(draft: MemoirBookChapterDraft | None) -> MemoirBookChapterDraftVersion | None:
    if not draft or not draft.versions:
        return None
    return draft.versions[-1]


def _output_version_response(version: MemoirBookOutputVersion) -> MemoirBookOutputVersionResponse:
    return MemoirBookOutputVersionResponse(
        id=version.id,
        output_id=version.output_id,
        project_id=version.project_id,
        plan_id=version.plan_id,
        version=version.version,
        content=version.content,
        chapter_snapshot_json=[
            item for item in (version.chapter_snapshot_json or [])
            if isinstance(item, dict)
        ],
        created_by=MemoirCreatedBy(version.created_by),
        created_at=version.created_at,
    )


def _output_response(
    output: MemoirBookOutput,
    *,
    stale_chapter_titles: list[str] | None = None,
) -> MemoirBookOutputResponse:
    versions = [_output_version_response(version) for version in output.versions]
    stale_titles = stale_chapter_titles or []
    return MemoirBookOutputResponse(
        id=output.id,
        project_id=output.project_id,
        plan_id=output.plan_id,
        title=output.title,
        status=MemoirOutputStatus(output.status),
        metadata_json=output.metadata_json or {},
        is_stale=bool(stale_titles),
        stale_chapter_titles=stale_titles,
        latest_version=versions[-1] if versions else None,
        versions=versions,
        created_at=output.created_at,
        updated_at=output.updated_at,
    )


async def _load_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookPlan:
    await load_memoir_project(db, project_id, user_id)
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
        .execution_options(populate_existing=True)
    )
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return plan


def _require_confirmed_chapters(plan: MemoirBookPlan) -> list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]]:
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")
    chapters = sorted(plan.chapters, key=lambda item: item.sort_order)
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")

    missing: list[str] = []
    confirmed: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]] = []
    for chapter in chapters:
        version = _latest_chapter_version(chapter.draft)
        if not chapter.draft or chapter.draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value or not version:
            missing.append(chapter.title)
            continue
        confirmed.append((chapter, version))
    if missing:
        preview = "、".join(missing[:3])
        suffix = "等章节" if len(missing) > 3 else ""
        raise HTTPException(status_code=400, detail=f"请先确认所有章节：{preview}{suffix}")
    return confirmed


def _compose_book_content(
    *,
    title: str,
    chapters: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]],
) -> tuple[str, list[dict[str, object]]]:
    toc = "\n".join(f"{index}. {chapter.title}" for index, (chapter, _) in enumerate(chapters, start=1))
    body_parts: list[str] = []
    snapshot: list[dict[str, object]] = []
    for index, (chapter, version) in enumerate(chapters, start=1):
        content = version.content.strip()
        body_parts.append(f"第{index}章 {chapter.title}\n\n{content}")
        snapshot.append(_chapter_snapshot(chapter, version))
    content = f"{title}\n\n目录\n{toc}\n\n" + "\n\n".join(body_parts)
    return content.strip(), snapshot


def _chapter_snapshot(chapter: MemoirBookPlanChapter, version: MemoirBookChapterDraftVersion) -> dict[str, object]:
    content = version.content.strip()
    return {
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "chapter_sort_order": chapter.sort_order,
        "draft_id": chapter.draft.id if chapter.draft else "",
        "draft_version_id": version.id,
        "draft_version": version.version,
        "content_char_count": len(content),
    }


def _current_book_snapshot(plan: MemoirBookPlan) -> tuple[list[dict[str, object]], list[str]]:
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        return [], ["章节规划"]
    chapters = sorted(plan.chapters, key=lambda item: item.sort_order)
    snapshot: list[dict[str, object]] = []
    unavailable_titles: list[str] = []
    for chapter in chapters:
        version = _latest_chapter_version(chapter.draft)
        if not chapter.draft or chapter.draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value or not version:
            unavailable_titles.append(chapter.title)
            continue
        snapshot.append(_chapter_snapshot(chapter, version))
    return snapshot, unavailable_titles


def _snapshot_stale_chapter_titles(
    latest_version: MemoirBookOutputVersion | None,
    current_snapshot: list[dict[str, object]],
    unavailable_titles: list[str],
) -> list[str]:
    if unavailable_titles:
        return unavailable_titles
    if not latest_version:
        return []
    stored_snapshot = [
        item for item in (latest_version.chapter_snapshot_json or [])
        if isinstance(item, dict)
    ]
    stored_by_chapter_id = {
        item.get("chapter_id"): item
        for item in stored_snapshot
        if isinstance(item.get("chapter_id"), str)
    }
    stale_titles: list[str] = []
    for current in current_snapshot:
        chapter_id = current.get("chapter_id")
        stored = stored_by_chapter_id.get(chapter_id)
        if not stored or any(stored.get(key) != current.get(key) for key in SNAPSHOT_COMPARE_KEYS):
            stale_titles.append(str(current.get("chapter_title") or "未命名章节"))
    if len(stored_snapshot) != len(current_snapshot) and not stale_titles:
        stale_titles.append("章节结构")
    return stale_titles


def _output_stale_chapter_titles(output: MemoirBookOutput, plan: MemoirBookPlan) -> list[str]:
    current_snapshot, unavailable_titles = _current_book_snapshot(plan)
    latest_version = output.versions[-1] if output.versions else None
    return _snapshot_stale_chapter_titles(latest_version, current_snapshot, unavailable_titles)


async def _latest_output_version_number(db: AsyncSession, output_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirBookOutputVersion.version)).where(
            MemoirBookOutputVersion.output_id == output_id
        )
    )
    return int(value or 0)


async def _load_output(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookOutput | None:
    return await db.scalar(
        select(MemoirBookOutput)
        .where(MemoirBookOutput.project_id == project_id)
        .options(selectinload(MemoirBookOutput.versions))
        .execution_options(populate_existing=True)
    )


async def get_book_output(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookOutputResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    plan = await _load_plan(db, user_id=user_id, project_id=project_id)
    return _output_response(output, stale_chapter_titles=_output_stale_chapter_titles(output, plan))


async def generate_book_output(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookOutputGenerateRequest,
) -> MemoirBookOutputResponse:
    plan = await _load_plan(db, user_id=user_id, project_id=project_id)
    chapters = _require_confirmed_chapters(plan)
    title = plan.title.replace("章节规划", "").strip() or "我的回忆录"
    content, snapshot = _compose_book_content(title=title, chapters=chapters)

    output = await _load_output(db, project_id=project_id)
    stale_titles = (
        _snapshot_stale_chapter_titles(output.versions[-1], snapshot, [])
        if output and output.versions
        else []
    )
    if output and output.versions and not req.force_regenerate and not stale_titles:
        return _output_response(output)

    if not output:
        output = MemoirBookOutput(
            project_id=project_id,
            plan_id=plan.id,
            title=title,
            status=MemoirOutputStatus.DRAFT.value,
            metadata_json={},
        )
        db.add(output)
        await db.flush()
    else:
        output.plan_id = plan.id
        output.title = title
        output.status = MemoirOutputStatus.DRAFT.value
        output.metadata_json = {
            **(output.metadata_json or {}),
            BOOK_TTS_CACHE_KEY: None,
        }

    version_number = await _latest_output_version_number(db, output.id) + 1
    db.add(
        MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=plan.id,
            version=version_number,
            content=content,
            chapter_snapshot_json=snapshot,
            created_by=MemoirCreatedBy.SYSTEM.value,
        )
    )
    await db.commit()
    refreshed = await _load_output(db, project_id=project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="全书稿保存失败")
    return _output_response(refreshed)


async def synthesize_book_output_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookAudioRequest,
    tts_client: TTSClient | None = None,
) -> MemoirBookAudioResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    text = latest.content.strip()
    text_hash = source_text_hash(text)
    cached = cached_audio_response(
        output.metadata_json or {},
        cache_key=BOOK_TTS_CACHE_KEY,
        text_hash=text_hash,
        voice=req.voice,
        force_regenerate=req.force_regenerate,
    )
    if cached:
        return cached

    synthesis = await synthesize_text_audio(
        db,
        user_id=user_id,
        text=text,
        voice=req.voice,
        tts_client=tts_client,
    )
    try:
        output.metadata_json = {
            **(output.metadata_json or {}),
            BOOK_TTS_CACHE_KEY: audio_cache_payload(synthesis, voice=req.voice),
        }
        db.add(output)
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio_files(synthesis.staged_audio)
        raise
    return audio_response(synthesis)
