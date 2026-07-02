from __future__ import annotations

import re

from app.models.memoir import MemoirBookPlanChapter


def chapter_source_count(
    chapter: MemoirBookPlanChapter,
    source_count_by_chapter: dict[str, int] | None = None,
) -> int:
    if source_count_by_chapter and chapter.id in source_count_by_chapter:
        return source_count_by_chapter[chapter.id]
    return len(chapter.source_refs_json or [])


def latest_draft_content(chapter: MemoirBookPlanChapter) -> str:
    draft = chapter.draft
    if not draft or not draft.versions:
        return ""
    return (draft.versions[-1].content or "").strip()


def draft_char_count(chapter: MemoirBookPlanChapter) -> int:
    return len(re.sub(r"\s+", "", latest_draft_content(chapter)))
