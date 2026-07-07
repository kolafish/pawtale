from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import (
    MemoirBookPlan,
)
from app.schemas.memoir_book import (
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookPlanResponse,
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
    load_plan_with_chapters as _load_plan,
    plan_response as _plan_response,
)
from app.services.memoir.book_source_links import (
    plan_source_ref_dicts_by_chapter as _plan_source_ref_dicts_by_chapter,
)
from app.services.memoir.voice_tts import TTSClient


BOOK_OUTLINE_TTS_CACHE_KEY = "outline_tts_audio"


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

    refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
    source_counts_by_chapter = {
        chapter_id: len(refs)
        for chapter_id, refs in refs_by_chapter.items()
    }
    text = build_book_outline_audio_text(plan, source_counts_by_chapter=source_counts_by_chapter)
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


def build_book_outline_audio_text(
    plan: MemoirBookPlan,
    *,
    source_counts_by_chapter: dict[str, int] | None = None,
) -> str:
    chapters = sorted(plan.chapters, key=lambda chapter: chapter.sort_order)
    title = (plan.title or "回忆录章节规划").strip() or "回忆录章节规划"
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划为空，无法朗读")

    lines = [f"《{title}》现在有 {len(chapters)} 章。"]
    for index, chapter in enumerate(chapters, start=1):
        chapter_title = (chapter.title or "未命名章节").strip() or "未命名章节"
        source_count = (
            source_counts_by_chapter.get(chapter.id, 0)
            if source_counts_by_chapter is not None
            else 0
        )
        lines.append(f"第 {index} 章，{chapter_title}。本章收录 {source_count} 条语音素材。")
    lines.append("你可以继续调整章节顺序、合并章节，或者说出想修改的地方。")
    return "\n".join(lines)
