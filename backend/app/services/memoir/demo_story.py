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
    MemoirMaterialShareVideo,
)
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookOutputResponse
from app.schemas.memoir_snapshots import (
    MemoirDemoBookResponse,
    MemoirDemoStoryResponse,
    MemoirDemoVideoResponse,
    MemoirStoryChapterResponse,
    MemoirStorySectionResponse,
)
from app.services.memoir import share_video_shares
from app.services.memoir.book_shares import public_reading_response
from app.services.memoir.story_snapshot import attach_sections, chapter_rows


def _configured(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def _demo_project(db: AsyncSession, project_id: str = "") -> tuple[Project, set[str]] | None:
    project_ids = _configured(get_settings().memoir_demo_project_ids)
    if project_id and project_id not in project_ids:
        raise HTTPException(status_code=404, detail="示例回忆录暂时无法阅读")
    project_id = project_id or next(iter(project_ids), "")
    project = await db.get(Project, project_id) if project_id else None
    if project is None or project.type != "memoir":
        return None
    readable_ids = set(await db.scalars(select(MemoirBookPlanChapter.id).where(
        MemoirBookPlanChapter.project_id == project.id,
        MemoirBookPlanChapter.include_in_book.is_(True),
    )))
    return project, readable_ids


async def demo_story(db: AsyncSession, project_id: str = "") -> MemoirDemoStoryResponse:
    demo = await _demo_project(db, project_id)
    if demo is None:
        return MemoirDemoStoryResponse()
    project, readable_ids = demo
    book = await _demo_book(db, project.id, readable_ids)
    published_chapters = {
        chapter.chapter_id: chapter for chapter in book.latest_version.manuscript.chapters
        if chapter.content.strip()
    } if book and book.latest_version and book.latest_version.manuscript else {}
    chapter_views = {}
    for chapter in await chapter_rows(db, project.id):
        if not chapter.include_in_book:
            continue
        readable = chapter.id in published_chapters
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
    # Readable cards describe the public book, not newer working sections.
    for chapter_id, published in published_chapters.items():
        if chapter_id not in chapter_views:
            continue
        view = chapter_views[chapter_id]
        view.title = published.title
        view.char_count = len(published.content.strip())
        view.sections = [MemoirStorySectionResponse(
            id=section.section_id, title=section.title, include_in_book=True, material_count=section.source_count,
        ) for section in published.sections]
        view.material_count = sum(section.material_count for section in view.sections)
    chapters = list(chapter_views.values())
    videos = []
    codes = _configured(get_settings().memoir_demo_video_codes)
    project_codes = set(await db.scalars(select(MemoirMaterialShareVideo.share_code).where(
        MemoirMaterialShareVideo.project_id == project.id,
        MemoirMaterialShareVideo.share_code.in_(codes),
    )))
    for code in dict.fromkeys(codes):
        if code not in project_codes:
            continue
        try:
            video = await share_video_shares.read_shared_video(db, code)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            continue  # A deleted video simply leaves the sample.
        videos.append(MemoirDemoVideoResponse(**video.model_dump(), share_code=code))
    books = []
    for demo_id in dict.fromkeys(_configured(get_settings().memoir_demo_project_ids)):
        entry = demo if demo_id == project.id else await _demo_project(db, demo_id)
        if entry is None:
            continue
        output = book if demo_id == project.id else await _demo_book(db, entry[0].id, entry[1])
        if output is None:
            continue
        manuscript = output.latest_version.manuscript
        books.append(MemoirDemoBookResponse(
            id=demo_id, title=output.title,
            book_chapter_count=len(manuscript.chapters),
            book_char_count=sum(len(chapter.content.strip()) for chapter in manuscript.chapters),
        ))
    return MemoirDemoStoryResponse(
        books=books,
        title=book.title if book else project.title,
        chapters=chapters,
        book_chapter_count=len(chapters),
        book_char_count=sum(chapter.char_count for chapter in chapters),
        videos=videos,
    )


async def _demo_book(
    db: AsyncSession, project_id: str, chapter_ids: set[str],
) -> MemoirBookOutputResponse | None:
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    version = await db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.output_id == output.id)
        .order_by(MemoirBookOutputVersion.version.desc())
        .limit(1)
    ) if output else None
    if version is None:
        return None
    result = public_reading_response(output, version, chapter_ids=chapter_ids, include_book_details=True)
    return result if result.latest_version and result.latest_version.content.strip() else None


async def demo_book(db: AsyncSession, project_id: str = "") -> MemoirBookOutputResponse:
    demo = await _demo_project(db, project_id)
    result = await _demo_book(db, demo[0].id, demo[1]) if demo else None
    if result is None:
        raise HTTPException(status_code=404, detail="示例回忆录暂时无法阅读")
    return result
