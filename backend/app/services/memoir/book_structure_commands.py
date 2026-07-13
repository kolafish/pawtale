from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from fastapi import HTTPException

from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import MemoirBookPlanStructureCommandRequest
from app.services.memoir.book_plan_sources import (
    SourceSegment,
    as_dict,
    find_chapter_by_id,
    sorted_chapters,
    text_or_empty,
)


@dataclass
class BookStructureCommandMutation:
    chapters: list[MemoirBookPlanChapter]
    source_refs_by_chapter: dict[str, list[dict[str, object]]]


def command_chapter_id(req: MemoirBookPlanStructureCommandRequest) -> str:
    if not req.chapter_id:
        raise HTTPException(status_code=400, detail="章节不能为空")
    return req.chapter_id


def source_utterance_id(req: MemoirBookPlanStructureCommandRequest) -> str:
    if not req.utterance_id:
        raise HTTPException(status_code=400, detail="讲述记录不能为空")
    return req.utterance_id


def chapter_by_id(plan: MemoirBookPlan, chapter_id: str) -> MemoirBookPlanChapter:
    chapter = find_chapter_by_id(sorted_chapters(plan), chapter_id)
    if chapter:
        return chapter
    raise HTTPException(status_code=404, detail="章节不存在")


def chapter_title_from_source(source: SourceSegment) -> str:
    text = "".join(text_or_empty(source.text).split())
    for prefix in ("新增讲述", "新收录讲述", "讲述记录", "新增转写", "新收录转写", "转写记录"):
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip(":：，,。")
            break
    first_clause = next((part for part in re.split(r"[，。！？、,.!?;；:：]", text) if part), text)
    if not first_clause or len(first_clause) < 3:
        return "补记往事"
    return first_clause[:16]


def apply_book_structure_command(
    *,
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
    project_id: str,
    now: str,
    source_refs_by_chapter: dict[str, list[dict[str, object]]],
    source: SourceSegment | None = None,
) -> BookStructureCommandMutation:
    if req.type == "move_chapter":
        return _move_chapter(chapters, req, source_refs_by_chapter=source_refs_by_chapter)
    if req.type == "create_chapter_from_source":
        if source is None:
            raise HTTPException(status_code=400, detail="讲述记录不属于当前回忆录")
        return _create_chapter_from_source(
            plan,
            chapters,
            req,
            project_id=project_id,
            now=now,
            source_refs_by_chapter=source_refs_by_chapter,
            source=source,
        )
    raise HTTPException(status_code=400, detail="不支持的章节结构操作")


def _move_chapter(
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
    *,
    source_refs_by_chapter: dict[str, list[dict[str, object]]],
) -> BookStructureCommandMutation:
    chapter_id = command_chapter_id(req)
    from_index = next((index for index, chapter in enumerate(chapters) if chapter.id == chapter_id), -1)
    if from_index < 0:
        raise HTTPException(status_code=404, detail="章节不存在")
    if req.direction:
        to_index = from_index - 1 if req.direction == "up" else from_index + 1
        if to_index < 0:
            raise HTTPException(status_code=400, detail="这一章已经在最前面")
        if to_index >= len(chapters):
            raise HTTPException(status_code=400, detail="这一章已经在最后面")
        next_chapters = list(chapters)
        next_chapters[from_index], next_chapters[to_index] = next_chapters[to_index], next_chapters[from_index]
        return BookStructureCommandMutation(chapters=next_chapters, source_refs_by_chapter=source_refs_by_chapter)

    next_chapters = [chapter for chapter in chapters if chapter.id != chapter_id]
    moving_chapter = chapters[from_index]
    to_index = _move_insert_index(chapters, next_chapters, req)
    next_chapters.insert(to_index, moving_chapter)
    if [chapter.id for chapter in next_chapters] == [chapter.id for chapter in chapters]:
        raise HTTPException(status_code=400, detail="这一章已经在目标位置")
    return BookStructureCommandMutation(chapters=next_chapters, source_refs_by_chapter=source_refs_by_chapter)


def _move_insert_index(
    original_chapters: list[MemoirBookPlanChapter],
    remaining_chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
) -> int:
    if req.target_position == "first":
        return 0
    if req.target_position == "last":
        return len(remaining_chapters)
    if req.target_index is not None:
        if req.target_index < 1 or req.target_index > len(original_chapters):
            raise HTTPException(status_code=400, detail="目标章节位置不存在")
        return min(req.target_index - 1, len(remaining_chapters))
    if req.relative_chapter_id:
        if req.relative_chapter_id == req.chapter_id:
            raise HTTPException(status_code=400, detail="不能移动到自身前后")
        if req.relative_position not in {"before", "after"}:
            raise HTTPException(status_code=400, detail="缺少相对章节位置")
        relative_index = next(
            (index for index, chapter in enumerate(remaining_chapters) if chapter.id == req.relative_chapter_id),
            -1,
        )
        if relative_index < 0:
            raise HTTPException(status_code=404, detail="目标章节不存在")
        return relative_index + (1 if req.relative_position == "after" else 0)
    raise HTTPException(status_code=400, detail="缺少章节移动目标")


def _create_chapter_from_source(
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
    *,
    project_id: str,
    now: str,
    source_refs_by_chapter: dict[str, list[dict[str, object]]],
    source: SourceSegment,
) -> BookStructureCommandMutation:
    new_chapter = MemoirBookPlanChapter(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        project_id=project_id,
        title=(req.title or chapter_title_from_source(source)).strip()[:200],
        sort_order=10000 + len(chapters),
        metadata_json={"created_from_source_at": now},
    )
    next_refs_by_chapter = dict(source_refs_by_chapter)
    next_refs_by_chapter[new_chapter.id] = [as_dict(source.ref())]
    return BookStructureCommandMutation(
        chapters=[*chapters, new_chapter],
        source_refs_by_chapter=next_refs_by_chapter,
    )
