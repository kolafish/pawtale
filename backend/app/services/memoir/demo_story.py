"""A sample memoir that visitors can browse before logging in."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlanChapter,
)
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookOutputResponse
from app.schemas.memoir_snapshots import (
    MemoirDemoStoryResponse,
    MemoirDemoVideoResponse,
    MemoirStoryChapterResponse,
)
from app.services.memoir import share_video_shares
from app.services.memoir.book_shares import public_reading_response
from app.services.memoir.story_snapshot import attach_sections, chapter_rows


def _configured(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def _demo_project(db: AsyncSession) -> tuple[Project, set[str]] | None:
    chapter_ids = _configured(get_settings().memoir_demo_chapter_ids)
    if not chapter_ids:
        return None
    project_id = await db.scalar(
        select(MemoirBookPlanChapter.project_id).where(MemoirBookPlanChapter.id == chapter_ids[0])
    )
    project = await db.get(Project, project_id) if project_id else None
    return (project, set(chapter_ids)) if project else None


async def demo_story(db: AsyncSession) -> MemoirDemoStoryResponse:
    demo = await _demo_project(db)
    if demo is None:
        return MemoirDemoStoryResponse()
    project, readable_ids = demo
    chapter_views = {}
    for chapter in await chapter_rows(db, project.id):
        if not chapter.include_in_book:
            continue
        readable = chapter.id in readable_ids and bool(chapter.char_count)
        chapter_views[chapter.id] = MemoirStoryChapterResponse(
            id=chapter.id,
            title=chapter.title,
            include_in_book=True,
            char_count=chapter.char_count or 0,
            readable=readable,
            state="settled" if readable else "draft",
            state_text="可阅读" if readable else "待整理",
        )
    await attach_sections(db, project.id, chapter_views)
    chapters = list(chapter_views.values())
    videos = []
    for code in _configured(get_settings().memoir_demo_video_codes):
        try:
            video = await share_video_shares.read_shared_video(db, code)
        except HTTPException:
            continue  # A deleted video simply leaves the sample.
        videos.append(MemoirDemoVideoResponse(**video.model_dump(), share_code=code))
    return MemoirDemoStoryResponse(
        title=project.title,
        chapters=chapters,
        book_chapter_count=len(chapters),
        book_char_count=sum(chapter.char_count for chapter in chapters),
        videos=videos,
    )


async def demo_book(db: AsyncSession) -> MemoirBookOutputResponse:
    demo = await _demo_project(db)
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == demo[0].id)
    ) if demo else None
    version = await db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.output_id == output.id)
        .order_by(MemoirBookOutputVersion.version.desc())
        .limit(1)
    ) if output else None
    if version is None:
        raise HTTPException(status_code=404, detail="示例回忆录暂时无法阅读")
    return public_reading_response(output, version, chapter_ids=demo[1])
