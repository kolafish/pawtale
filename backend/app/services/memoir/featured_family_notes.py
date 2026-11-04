"""Reader-facing attribution, attached at read/export time so unfeaturing takes effect."""
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirShareResponse
from app.schemas.memoir_book import MemoirBookManuscript
from app.schemas.memoir_share_responses import FeaturedFamilyNote


async def chapter_notes(db: AsyncSession, project_id: str, chapter_ids: Sequence[str]) -> dict[str, list[FeaturedFamilyNote]]:
    if not chapter_ids:
        return {}
    rows = await db.execute(select(MemoirShareResponse.featured_chapter_id, MemoirShareResponse.display_name,
        MemoirShareResponse.content).where(MemoirShareResponse.project_id == project_id,
        MemoirShareResponse.featured_chapter_id.in_(chapter_ids)).order_by(MemoirShareResponse.created_at, MemoirShareResponse.id))
    result: dict[str, list[FeaturedFamilyNote]] = {}
    for chapter_id, name, content in rows:
        result.setdefault(chapter_id, []).append(FeaturedFamilyNote(display_name=name, content=content))
    return result


async def attach_chapter_notes(db: AsyncSession, project_id: str, manuscript: MemoirBookManuscript | None) -> None:
    if manuscript:
        notes = await chapter_notes(db, project_id, [chapter.chapter_id for chapter in manuscript.chapters])
        for chapter in manuscript.chapters:
            chapter.family_notes = notes.get(chapter.chapter_id, [])


def export_text_with_notes(content: str, manuscript: MemoirBookManuscript) -> str:
    """Insert notes into the saved text, including older books without a generated TOC."""
    if not any(chapter.family_notes for chapter in manuscript.chapters):
        return content
    end = len(content)
    unattached = []
    for chapter in reversed(manuscript.chapters):
        heading = f"第{chapter.index}章 {chapter.title}"
        start = content.rfind(f"\n{heading}\n", 0, end)
        if start < 0 and content.startswith(heading + "\n"):
            start = 0
        if chapter.family_notes:
            notes = "\n\n".join(f"{note.display_name}：{note.content}" for note in chapter.family_notes)
            if start >= 0 or len(manuscript.chapters) == 1:
                content = content[:end].rstrip() + "\n\n家人留言\n" + notes + content[end:]
            else:
                # Unstructured legacy text has no reliable per-chapter boundary.
                unattached.append(f"{heading} · 家人留言\n{notes}")
        if start >= 0:
            end = start
    return "\n\n".join([content, *reversed(unattached)])
