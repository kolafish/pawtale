from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.memoir import MemoirBookChapterDraft, MemoirBookChapterDraftVersion, MemoirBookPlan, MemoirBookPlanChapter


def latest_chapter_version(draft: MemoirBookChapterDraft | None) -> MemoirBookChapterDraftVersion | None:
    return max(draft.versions, key=lambda version: (version.version, version.created_at, version.id)) if draft and draft.versions else None


async def load_book_plan_drafts(db: AsyncSession, *, project_id: str) -> MemoirBookPlan:
    plan = await db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id).options(
        selectinload(MemoirBookPlan.chapters).selectinload(MemoirBookPlanChapter.draft).selectinload(MemoirBookChapterDraft.versions),
    ).execution_options(populate_existing=True))
    if plan is None:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return plan


def readable_chapter_versions(plan: MemoirBookPlan, *, allow_partial: bool = False) -> list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]]:
    chapters = sorted((chapter for chapter in plan.chapters if chapter.include_in_book), key=lambda chapter: (chapter.sort_order, chapter.id))
    result = []
    for chapter in chapters:
        version = latest_chapter_version(chapter.draft)
        if version is None or not version.content.strip() or not normalized_section_snapshots(version.section_snapshot_json):
            if allow_partial:
                continue
            raise HTTPException(status_code=409, detail=f"章节“{chapter.title}”还没有可读正文")
        result.append((chapter, version))
    if not result and not allow_partial:
        raise HTTPException(status_code=409, detail="还没有可读的章节正文")
    return result


def _safe_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalized_section_snapshots(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    sections: list[dict[str, object]] = []
    for fallback_index, raw_section in enumerate(value, start=1):
        if not isinstance(raw_section, dict):
            continue
        section_id = _safe_str(raw_section.get("section_id"))
        title = _safe_str(raw_section.get("title"))
        content = _safe_str(raw_section.get("content"))
        if not section_id or not title or not content:
            continue
        raw_material_ids = raw_section.get("used_material_ids")
        used_material_ids = [
            material_id.strip()
            for material_id in raw_material_ids
            if isinstance(material_id, str) and material_id.strip()
        ] if isinstance(raw_material_ids, list) else []
        sections.append(
            {
                "section_id": section_id,
                "section_index": fallback_index,
                "title": title,
                "section_type": _safe_str(raw_section.get("section_type")),
                "section_version": raw_section.get("section_version")
                if isinstance(raw_section.get("section_version"), int)
                else 0,
                "sort_order": raw_section.get("sort_order")
                if isinstance(raw_section.get("sort_order"), int)
                else fallback_index - 1,
                "content": content,
                "content_char_count": len(content),
                "source_count": len(used_material_ids),
                "used_material_ids": used_material_ids,
            }
        )
    return sections


def sectioned_chapter_content(sections: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for section in sections:
        title = _safe_str(section.get("title"))
        content = _safe_str(section.get("content"))
        if title and content:
            parts.append(f"{title}\n\n{content}")
    return "\n\n".join(parts)


def book_content_from_chapter_snapshots(
    *,
    title: str,
    snapshots: list[dict[str, object]],
) -> str:
    toc_lines: list[str] = []
    body_parts: list[str] = []
    for fallback_index, snapshot in enumerate(snapshots, start=1):
        chapter_index = (
            snapshot.get("chapter_index")
            if isinstance(snapshot.get("chapter_index"), int)
            else fallback_index
        )
        chapter_title = (
            _safe_str(snapshot.get("chapter_title"))
            or _safe_str(snapshot.get("title"))
            or f"第{chapter_index}章"
        )
        toc_lines.append(f"{chapter_index}. {chapter_title}")
        raw_sections = snapshot.get("sections")
        sections = [
            section
            for section in raw_sections
            if isinstance(section, dict)
        ] if isinstance(raw_sections, list) else []
        for section_index, section in enumerate(sections, start=1):
            section_title = _safe_str(section.get("title"))
            if section_title:
                toc_lines.append(f"  {chapter_index}.{section_index} {section_title}")
        chapter_content = sectioned_chapter_content(sections) or _safe_str(
            snapshot.get("content")
        )
        if chapter_content:
            body_parts.append(
                f"第{chapter_index}章 {chapter_title}\n\n{chapter_content}"
            )
    toc = "\n".join(toc_lines)
    return (f"{title}\n\n目录\n{toc}\n\n" + "\n\n".join(body_parts)).strip()


def chapter_snapshot(
    chapter: MemoirBookPlanChapter,
    version: MemoirBookChapterDraftVersion,
    *,
    chapter_index: int,
    source_refs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    content = version.content.strip()
    sections = normalized_section_snapshots(version.section_snapshot_json)
    refs = source_refs or []
    source_ref_snapshot = [
        {
            "utterance_id": _safe_str(item.get("utterance_id")),
            "transcript_version_id": _safe_str(item.get("transcript_version_id"))
            or None,
            "voice_session_id": _safe_str(item.get("voice_session_id")) or None,
            "quote": _safe_str(item.get("quote")),
            "summary": _safe_str(item.get("summary"))
            or _safe_str(item.get("source_summary")),
            "representative_quote": _safe_str(item.get("representative_quote"))
            or _safe_str(item.get("quote")),
        }
        for item in refs
        if isinstance(item, dict) and _safe_str(item.get("utterance_id"))
    ]
    chapter_metadata = (
        chapter.metadata_json if isinstance(chapter.metadata_json, dict) else {}
    )
    return {
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "chapter_index": chapter_index,
        "chapter_sort_order": chapter.sort_order,
        "chapter_summary": (
            _safe_str(chapter_metadata.get("chapter_summary"))
            or _safe_str(chapter_metadata.get("summary"))
            or _safe_str(chapter_metadata.get("editorial_summary"))
        ),
        "metadata_json": chapter_metadata,
        "draft_id": version.draft_id,
        "draft_version_id": version.id,
        "draft_version": version.version,
        "content_char_count": len(content),
        "source_count": len(source_ref_snapshot),
        "source_refs": source_ref_snapshot,
        "section_count": len(sections),
        "sections": sections,
        "content": content,
    }
