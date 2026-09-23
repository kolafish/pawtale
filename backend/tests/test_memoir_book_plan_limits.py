import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.domain.memoir.limits import MAX_MEMOIR_BOOK_PLAN_CHAPTERS
from app.schemas.memoir_book import (
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanStructureCommandRequest,
    MemoirBookPlanUpdateRequest,
)
from app.services.memoir.book_plan_ai import build_plan_prompt, chapters_from_payload


def _chapters(count: int) -> list[dict[str, str]]:
    return [{"title": f"第 {index} 章"} for index in range(count)]


def test_book_plan_writes_allow_256_chapters_and_reject_257():
    chapters = _chapters(MAX_MEMOIR_BOOK_PLAN_CHAPTERS)
    assert len(MemoirBookPlanConfirmRequest(chapters=chapters).chapters) == MAX_MEMOIR_BOOK_PLAN_CHAPTERS
    assert len(MemoirBookPlanUpdateRequest(chapters=chapters).chapters) == MAX_MEMOIR_BOOK_PLAN_CHAPTERS
    assert MemoirBookPlanStructureCommandRequest(
        type="move_chapter",
        chapter_id="chapter-1",
        target_index=MAX_MEMOIR_BOOK_PLAN_CHAPTERS,
    ).target_index == MAX_MEMOIR_BOOK_PLAN_CHAPTERS

    with pytest.raises(ValidationError):
        MemoirBookPlanConfirmRequest(chapters=_chapters(MAX_MEMOIR_BOOK_PLAN_CHAPTERS + 1))
    with pytest.raises(ValidationError):
        MemoirBookPlanUpdateRequest(chapters=_chapters(MAX_MEMOIR_BOOK_PLAN_CHAPTERS + 1))
    with pytest.raises(ValidationError):
        MemoirBookPlanStructureCommandRequest(
            type="move_chapter",
            chapter_id="chapter-1",
            target_index=MAX_MEMOIR_BOOK_PLAN_CHAPTERS + 1,
        )

    prompt = build_plan_prompt("我的回忆录", [])
    assert "最多章节数" not in prompt
    with pytest.raises(HTTPException, match="超过 256 章上限"):
        chapters_from_payload(
            {"title": "我的故事", "chapters": [{}] * (MAX_MEMOIR_BOOK_PLAN_CHAPTERS + 1)},
            sources=[],
        )
