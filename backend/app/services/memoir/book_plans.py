from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import (
    MemoirBookPlan,
)
from app.schemas.memoir_book import (
    MemoirAudioPlanRequest,
    MemoirBookAudioPlanResponse,
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookPlanResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio_jobs import AudioSource, complete_audio_plan, prepare_audio_plan
from app.services.memoir.book_plan_sources import (
    load_plan_with_chapters as _load_plan,
    plan_response as _plan_response,
)
from app.services.memoir.book_source_links import (
    plan_source_ref_dicts_by_chapter as _plan_source_ref_dicts_by_chapter,
)


async def get_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return await _plan_response(db, plan)


async def _outline_audio_source(
    db: AsyncSession, *, user_id: str, project_id: str,
    req: MemoirAudioPlanRequest, allow_admin: bool = False,
) -> AudioSource:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(404, "章节规划不存在")
    if req.version_id and req.version_id != plan.id:
        raise HTTPException(409, "章节规划已变化，请重新发起朗读")
    refs = await _plan_source_ref_dicts_by_chapter(db, plan)
    text = build_book_outline_audio_text(plan, source_counts_by_chapter={key: len(value) for key, value in refs.items()})
    return AudioSource(plan.id, text)


async def prepare_book_outline_audio(
    db: AsyncSession, *, user_id: str, project_id: str,
    req: MemoirAudioPlanRequest, allow_admin: bool = False,
) -> MemoirBookAudioPlanResponse:
    source = await _outline_audio_source(db, user_id=user_id, project_id=project_id, req=req, allow_admin=allow_admin)
    return await prepare_audio_plan(db, user_id=user_id, project_id=project_id, source=source, req=req, allow_admin=allow_admin)


async def synthesize_book_outline_audio(
    db: AsyncSession, *, user_id: str, project_id: str,
    req: MemoirBookAudioRequest, allow_admin: bool = False,
) -> MemoirBookAudioResponse:
    plan_req = MemoirAudioPlanRequest(**req.model_dump(), retry_failed=True)
    source = await _outline_audio_source(db, user_id=user_id, project_id=project_id, req=plan_req, allow_admin=allow_admin)
    return await complete_audio_plan(db, user_id=user_id, project_id=project_id, source=source, req=plan_req, allow_admin=allow_admin)


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
        if not chapter.include_in_book:
            lines.append("本章目前保留整理，但不收入最终成书。")
    lines.append("你可以继续调整章节顺序、合并章节，或者说出想修改的地方。")
    return "\n".join(lines)
