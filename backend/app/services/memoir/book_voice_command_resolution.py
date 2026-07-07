from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.models.memoir import MemoirBookPlanChapter
from app.schemas.memoir_book import (
    MemoirBookVoiceCommandBookContext,
    MemoirBookVoiceCommandUncoveredSourceContext,
)
from app.services.memoir.book_voice_command_rules import (
    CURRENT_SOURCE_REFS,
    _chapter_number,
    _clean_chapter_ref,
    _compact,
    _source_ref_number,
)


def latest_source_ref_from_context(conversation_context: list[Any] | None) -> str | None:
    for item in reversed(conversation_context or []):
        if getattr(item, "role", "") != "assistant":
            continue
        text = _compact(getattr(item, "text", "") or "")
        match = re.search(r"待收录第\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*条", text)
        if match:
            return f"待收录第{match.group(1)}条"
        match = re.search(r"语音记录\s*([0-9]{1,3})", text)
        if match:
            return f"语音记录{match.group(1)}"
    return None


def resolve_uncovered_source(
    book_context: MemoirBookVoiceCommandBookContext | None,
    source_ref: str | None,
    conversation_context: list[Any] | None,
) -> MemoirBookVoiceCommandUncoveredSourceContext | None:
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    if not sources:
        return None
    ref = _compact(source_ref or "")
    if ref in set(CURRENT_SOURCE_REFS) or not ref:
        ref = latest_source_ref_from_context(conversation_context) or ref
    number = _source_ref_number(ref)
    if number is not None:
        if "语音记录" in ref or "记录编号" in ref:
            return next((source for source in sources if source.source_index == number), None)
        if 1 <= number <= len(sources):
            return sources[number - 1]
        return next((source for source in sources if source.source_index == number), None)
    return None


def source_list_position(
    book_context: MemoirBookVoiceCommandBookContext | None,
    source: MemoirBookVoiceCommandUncoveredSourceContext,
) -> int:
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    for index, item in enumerate(sources, start=1):
        if item.utterance_id == source.utterance_id:
            return index
    return 1


def resolve_chapter(
    chapters: list[MemoirBookPlanChapter],
    ref: str | None,
    current_chapter_id: str | None,
) -> MemoirBookPlanChapter | None:
    text = _clean_chapter_ref(ref or "")
    if text in {"本章", "这一章", "当前章", "当前章节"} and current_chapter_id:
        return next((chapter for chapter in chapters if chapter.id == current_chapter_id), None)
    chapter_number = _chapter_number(text)
    if chapter_number is not None and 1 <= chapter_number <= len(chapters):
        return chapters[chapter_number - 1]
    if not text:
        return None
    for chapter in chapters:
        title = _compact(chapter.title)
        if text == title or text in title or title in text:
            return chapter
    fuzzy_match = _fuzzy_chapter_match(chapters, text)
    if fuzzy_match:
        return fuzzy_match
    return None


def _fuzzy_chapter_match(chapters: list[MemoirBookPlanChapter], text: str) -> MemoirBookPlanChapter | None:
    if len(text) < 3:
        return None
    scored: list[tuple[float, MemoirBookPlanChapter]] = []
    for chapter in chapters:
        title = _compact(chapter.title)
        if not title:
            continue
        score = SequenceMatcher(None, text, title).ratio()
        scored.append((score, chapter))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return None
    best_score, best_chapter = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.72 or (best_score >= 0.62 and best_score - second_score >= 0.16):
        return best_chapter
    return None
