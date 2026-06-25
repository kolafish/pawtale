from __future__ import annotations

import re
from dataclasses import dataclass, field

from fastapi import HTTPException

from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import MemoirBookPlanStructureCommandRequest
from app.services.memoir.book_plan_sources import (
    SourceSegment,
    as_dict,
    find_chapter_by_id,
    pending_source_ids,
    removed_source_ids,
    set_source_change_metadata,
    source_ref_utterance_id,
    source_ref_utterance_ids,
    text_or_empty,
    unique_source_ref_dicts,
)


@dataclass
class BookStructureCommandMutation:
    chapters: list[MemoirBookPlanChapter]
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


def source_utterance_id(req: MemoirBookPlanStructureCommandRequest) -> str:
    if not req.utterance_id:
        raise HTTPException(status_code=400, detail="讲述记录不能为空")
    return req.utterance_id


def chapter_by_id(plan: MemoirBookPlan, chapter_id: str) -> MemoirBookPlanChapter:
    chapter = find_chapter_by_id(plan.chapters, chapter_id)
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
    source: SourceSegment | None = None,
) -> BookStructureCommandMutation:
    if req.type == "move_chapter":
        return _move_chapter(chapters, req)
    if req.type == "delete_chapter":
        return _delete_chapter(plan, chapters, req)
    if req.type == "merge_chapters":
        return _merge_chapters(plan, chapters, req)
    if req.type == "split_chapter":
        return _split_chapter(plan, chapters, req, project_id=project_id, now=now)
    if req.type == "create_chapter_from_source":
        if source is None:
            raise HTTPException(status_code=400, detail="讲述记录不属于当前回忆录")
        return _create_chapter_from_source(plan, chapters, req, project_id=project_id, now=now, source=source)
    raise HTTPException(status_code=400, detail="不支持的章节结构操作")


def _move_chapter(
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
) -> BookStructureCommandMutation:
    chapter_id = command_chapter_id(req)
    if not req.direction:
        raise HTTPException(status_code=400, detail="移动方向不能为空")
    from_index = next((index for index, chapter in enumerate(chapters) if chapter.id == chapter_id), -1)
    to_index = from_index - 1 if req.direction == "up" else from_index + 1
    if from_index < 0:
        raise HTTPException(status_code=404, detail="章节不存在")
    if to_index < 0:
        raise HTTPException(status_code=400, detail="这一章已经在最前面")
    if to_index >= len(chapters):
        raise HTTPException(status_code=400, detail="这一章已经在最后面")
    next_chapters = list(chapters)
    next_chapters[from_index], next_chapters[to_index] = next_chapters[to_index], next_chapters[from_index]
    return BookStructureCommandMutation(chapters=next_chapters)


def _delete_chapter(
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
) -> BookStructureCommandMutation:
    chapter_id = command_chapter_id(req)
    if len(chapters) <= 1:
        raise HTTPException(status_code=400, detail="至少需要保留一个章节")
    chapter = chapter_by_id(plan, chapter_id)
    return BookStructureCommandMutation(
        chapters=[item for item in chapters if item.id != chapter_id],
        deleted_chapters=[chapter],
    )


def _merge_chapters(
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
) -> BookStructureCommandMutation:
    source_chapter_id = command_chapter_id(req)
    target_chapter_id_value = target_chapter_id(req)
    if source_chapter_id == target_chapter_id_value:
        raise HTTPException(status_code=400, detail="不能合并到同一章节")
    source_chapter = chapter_by_id(plan, source_chapter_id)
    target_chapter = chapter_by_id(plan, target_chapter_id_value)
    old_target_ids = set(source_ref_utterance_ids(target_chapter.source_refs_json or []))
    merged_refs = unique_source_ref_dicts([
        *(target_chapter.source_refs_json or []),
        *(source_chapter.source_refs_json or []),
    ])
    new_target_ids = set(source_ref_utterance_ids(merged_refs))
    added_ids = new_target_ids - old_target_ids
    changed_chapter_ids: set[str] = set()
    target_chapter.source_refs_json = merged_refs
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
    return BookStructureCommandMutation(
        chapters=[item for item in chapters if item.id != source_chapter_id],
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
) -> BookStructureCommandMutation:
    chapter_id = command_chapter_id(req)
    selected_ids = set(req.utterance_ids)
    if not selected_ids:
        raise HTTPException(status_code=400, detail="请选择要拆出的讲述记录")
    chapter = chapter_by_id(plan, chapter_id)
    kept_refs: list[dict] = []
    split_refs: list[dict] = []
    for ref in chapter.source_refs_json or []:
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
    chapter.source_refs_json = kept_refs
    chapter.metadata_json = set_source_change_metadata(
        chapter.metadata_json,
        added_ids=source_pending_ids,
        removed_ids=source_removed_ids,
    )

    source_index = chapters.index(chapter)
    new_chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title=(req.title or f"{chapter.title}（拆出）").strip()[:200],
        sort_order=10000 + len(chapters),
        source_refs_json=split_refs,
        metadata_json={"created_from_split_at": now, "split_from_chapter_id": chapter.id},
    )
    return BookStructureCommandMutation(
        chapters=[*chapters[:source_index + 1], new_chapter, *chapters[source_index + 1:]],
        changed_chapter_ids={chapter.id},
    )


def _create_chapter_from_source(
    plan: MemoirBookPlan,
    chapters: list[MemoirBookPlanChapter],
    req: MemoirBookPlanStructureCommandRequest,
    *,
    project_id: str,
    now: str,
    source: SourceSegment,
) -> BookStructureCommandMutation:
    new_chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title=(req.title or chapter_title_from_source(source)).strip()[:200],
        sort_order=10000 + len(chapters),
        source_refs_json=[as_dict(source.ref())],
        metadata_json={"created_from_source_at": now},
    )
    return BookStructureCommandMutation(chapters=[*chapters, new_chapter])
