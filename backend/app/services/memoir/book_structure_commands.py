from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException

from app.domain.memoir.limits import MAX_MEMOIR_BOOK_PLAN_CHAPTERS
from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import MemoirBookPlanStructureCommandRequest
from app.services.memoir.book_plan_sources import (
    find_chapter_by_id,
    pending_source_ids,
    removed_source_ids,
    set_source_change_metadata,
    source_ref_utterance_id,
    source_ref_utterance_ids,
    unique_source_ref_dicts,
)


@dataclass
class BookStructureCommandMutation:
    chapters: list[MemoirBookPlanChapter]
    source_refs_by_chapter: dict[str, list[dict[str, object]]]
    changed_chapter_ids: set[str] = field(default_factory=set)
    deleted_chapters: list[MemoirBookPlanChapter] = field(default_factory=list)


def command_chapter_id(req: MemoirBookPlanStructureCommandRequest) -> str:
    if not req.chapter_id:
        raise HTTPException(status_code=400, detail="章节不能为空")
    return req.chapter_id


def target_chapter_id(req: MemoirBookPlanStructureCommandRequest) -> str:
    if not req.target_chapter_id:
        raise HTTPException(status_code=400, detail="目标章节不能为空")
    return req.target_chapter_id


def chapter_by_id(plan: MemoirBookPlan, chapter_id: str) -> MemoirBookPlanChapter:
    chapter = find_chapter_by_id(plan.chapters, chapter_id)
    if chapter:
        return chapter
    raise HTTPException(status_code=404, detail="章节不存在")


def apply_book_structure_command(
    *,
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
    project_id: str,
    now: str,
    source_refs_by_chapter: dict[str, list[dict[str, object]]],
) -> BookStructureCommandMutation:
    if req.type == "move_chapter":
        return _move_chapter(chapters, req, source_refs_by_chapter=source_refs_by_chapter)
    if req.type == "delete_chapter":
        return _delete_chapter(plan, chapters, req, source_refs_by_chapter=source_refs_by_chapter)
    if req.type == "merge_chapters":
        return _merge_chapters(plan, chapters, req, source_refs_by_chapter=source_refs_by_chapter)
    if req.type == "split_chapter":
        if len(chapters) >= MAX_MEMOIR_BOOK_PLAN_CHAPTERS:
            raise HTTPException(
                status_code=400,
                detail=f"章节数量不能超过 {MAX_MEMOIR_BOOK_PLAN_CHAPTERS} 章",
            )
        return _split_chapter(
            plan,
            chapters,
            req,
            project_id=project_id,
            now=now,
            source_refs_by_chapter=source_refs_by_chapter,
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


def _delete_chapter(
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
    *,
    source_refs_by_chapter: dict[str, list[dict[str, object]]],
) -> BookStructureCommandMutation:
    chapter_id = command_chapter_id(req)
    if len(chapters) <= 1:
        raise HTTPException(status_code=400, detail="至少需要保留一个章节")
    chapter = chapter_by_id(plan, chapter_id)
    if chapter.include_in_book and sum(item.include_in_book for item in chapters) <= 1:
        raise HTTPException(status_code=400, detail="成书时至少需要保留一个章节")
    next_refs_by_chapter = dict(source_refs_by_chapter)
    next_refs_by_chapter.pop(chapter.id, None)
    return BookStructureCommandMutation(
        chapters=[item for item in chapters if item.id != chapter_id],
        source_refs_by_chapter=next_refs_by_chapter,
        deleted_chapters=[chapter],
    )


def _merge_chapters(
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
    *,
    source_refs_by_chapter: dict[str, list[dict[str, object]]],
) -> BookStructureCommandMutation:
    source_chapter_id = command_chapter_id(req)
    target_chapter_id_value = target_chapter_id(req)
    if source_chapter_id == target_chapter_id_value:
        raise HTTPException(status_code=400, detail="不能合并到同一章节")
    source_chapter = chapter_by_id(plan, source_chapter_id)
    target_chapter = chapter_by_id(plan, target_chapter_id_value)
    target_chapter.include_in_book = (
        target_chapter.include_in_book or source_chapter.include_in_book
    )
    source_refs = source_refs_by_chapter.get(source_chapter.id, [])
    target_refs = source_refs_by_chapter.get(target_chapter.id, [])
    old_target_ids = set(source_ref_utterance_ids(target_refs))
    merged_refs = unique_source_ref_dicts([
        *target_refs,
        *source_refs,
    ])
    new_target_ids = set(source_ref_utterance_ids(merged_refs))
    added_ids = new_target_ids - old_target_ids
    changed_chapter_ids: set[str] = set()
    if added_ids:
        target_pending_ids = pending_source_ids(target_chapter.metadata_json)
        target_pending_ids.update(added_ids)
        target_removed_ids = removed_source_ids(target_chapter.metadata_json)
        target_removed_ids.difference_update(new_target_ids)
        target_chapter.metadata_json = set_source_change_metadata(
            target_chapter.metadata_json,
            added_ids=target_pending_ids,
            removed_ids=target_removed_ids,
        )
        changed_chapter_ids.add(target_chapter.id)
    next_refs_by_chapter = dict(source_refs_by_chapter)
    next_refs_by_chapter[target_chapter.id] = merged_refs
    next_refs_by_chapter.pop(source_chapter.id, None)
    return BookStructureCommandMutation(
        chapters=[item for item in chapters if item.id != source_chapter_id],
        source_refs_by_chapter=next_refs_by_chapter,
        changed_chapter_ids=changed_chapter_ids,
        deleted_chapters=[source_chapter],
    )


def _split_chapter(
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
    *,
    project_id: str,
    now: str,
    source_refs_by_chapter: dict[str, list[dict[str, object]]],
) -> BookStructureCommandMutation:
    chapter_id = command_chapter_id(req)
    selected_ids = set(req.utterance_ids)
    if not selected_ids:
        raise HTTPException(status_code=400, detail="请选择要拆出的讲述记录")
    chapter = chapter_by_id(plan, chapter_id)
    kept_refs: list[dict] = []
    split_refs: list[dict] = []
    for ref in source_refs_by_chapter.get(chapter.id, []):
        utterance_id = source_ref_utterance_id(ref)
        if utterance_id in selected_ids:
            split_refs.append(dict(ref))
        else:
            kept_refs.append(dict(ref))
    if not split_refs:
        raise HTTPException(status_code=404, detail="没有找到要拆出的来源")
    if not kept_refs:
        raise HTTPException(status_code=400, detail="拆分时至少保留 1 条素材在原章节")

    kept_ids = set(source_ref_utterance_ids(kept_refs))
    split_ids = set(source_ref_utterance_ids(split_refs))
    source_pending_ids = pending_source_ids(chapter.metadata_json) & kept_ids
    source_removed_ids = removed_source_ids(chapter.metadata_json)
    source_removed_ids.update(split_ids)
    chapter.metadata_json = set_source_change_metadata(
        chapter.metadata_json,
        added_ids=source_pending_ids,
        removed_ids=source_removed_ids,
    )

    source_index = chapters.index(chapter)
    new_chapter = MemoirBookPlanChapter(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        project_id=project_id,
        title=(req.title or f"{chapter.title}（拆出）").strip()[:200],
        sort_order=10000 + len(chapters),
        include_in_book=chapter.include_in_book,
        metadata_json={"created_from_split_at": now, "split_from_chapter_id": chapter.id},
    )
    next_refs_by_chapter = dict(source_refs_by_chapter)
    next_refs_by_chapter[chapter.id] = kept_refs
    next_refs_by_chapter[new_chapter.id] = split_refs
    return BookStructureCommandMutation(
        chapters=[*chapters[:source_index + 1], new_chapter, *chapters[source_index + 1:]],
        source_refs_by_chapter=next_refs_by_chapter,
        changed_chapter_ids={chapter.id},
    )
