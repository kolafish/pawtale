from datetime import datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.domain.memoir.limits import MAX_MEMOIR_BOOK_PLAN_CHAPTERS
from app.domain.memoir.types import MemoirSectionMaterialAssignedBy
from app.schemas.memoir_book import (
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanStructureCommandRequest,
    MemoirBookPlanUpdateRequest,
)
from app.services.memoir.book_plan_ai import build_plan_prompt, chapters_from_payload
from app.services.memoir.book_plan_sources import SourceSegment
from app.services.memoir.sections import sync_materials_to_chapters


@pytest.mark.parametrize("payload", [
    {"chapters": []},
    {"chapters": [], "not_story_source_indices": [2]},
    {"chapters": [{"title": "往事", "source_indices": [1]}], "not_story_source_indices": [1]},
])
async def test_model_omission_or_conflicting_exclusion_never_marks_source_as_nonstory(payload):
    from app.services.memoir.book_plan_ai import generate_book_plan_from_sources
    from tests.test_memoir_section_organization import FakeStoryGrowthAI
    fake = FakeStoryGrowthAI(payload)
    with pytest.raises(HTTPException) as error:
        await generate_book_plan_from_sources(
            project_title="我的回忆录", sources=[SourceSegment(1, "session", "message", "utterance", None, None, None, None,
                "我叫余建国，小时候奶奶背我赶集。", datetime(2026, 10, 14))], interviewer_lines={}, ai_model=None, ai_client=fake,
        )
    assert error.value.status_code == 503
    assert fake.calls == 2


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


@pytest.mark.parametrize("later_indices", [[1], [1, 2]])
def test_generated_plan_keeps_whole_material_in_only_one_chapter(later_indices):
    sources = [
        SourceSegment(
            source_index=index,
            voice_session_id="session",
            message_id=f"message-{index}",
            utterance_id=f"utterance-{index}",
            transcript_version_id=None,
            audio_file_id=None,
            start_ms=0,
            end_ms=24101,
            text="河边晨步、雨衣和一碗热汤面。",
            created_at=datetime(2026, 10, 4),
        )
        for index in (1, 2)
    ]
    _, chapters = chapters_from_payload(
        {"chapters": [
            {"title": "晨步", "source_indices": [1, 1]},
            {"title": "雨衣", "source_indices": later_indices},
            {"title": "热汤面", "source_indices": [1]},
        ]},
        sources=sources,
    )

    assert [chapter.title for chapter in chapters] == (
        ["晨步"] if later_indices == [1] else ["晨步", "雨衣"]
    )
    assert [ref.utterance_id for chapter in chapters for ref in chapter.source_refs] == (
        ["utterance-1"] if later_indices == [1] else ["utterance-1", "utterance-2"]
    )


async def test_plan_material_sync_rejects_cross_chapter_refs_before_mutating():
    with pytest.raises(HTTPException) as rejected:
        await sync_materials_to_chapters(
            None,
            project_id="project",
            utterance_ids_by_chapter={"first": ["utterance"], "second": ["utterance"]},
            assigned_by=MemoirSectionMaterialAssignedBy.AI,
            assignment_reason="replace_book_plan",
        )
    assert rejected.value.status_code == 400


@pytest.mark.parametrize("chapters", [
    [{"title": "童年", "source_indices": [1]}],
    [{"title": "童年", "source_indices": [1, 2]}, {"title": "求学", "source_indices": [2]}],
    [{"title": "童年", "source_indices": [1]}, {"title": "", "source_indices": [2]}],
])
async def test_partial_initial_plan_keeps_unambiguous_sources_without_dropping_unresolved(chapters):
    from app.services.memoir.book_plan_ai import generate_book_plan_from_sources
    from tests.test_memoir_section_organization import FakeStoryGrowthAI
    sources = [SourceSegment(i, "session", f"message-{i}", f"utterance-{i}", None, None, None, None,
        "小时候奶奶背我赶集。", datetime(2026, 10, 14)) for i in (1, 2)]
    fake = FakeStoryGrowthAI({"chapters": chapters})
    result = await generate_book_plan_from_sources(project_title="我的回忆录", sources=sources,
        interviewer_lines={}, ai_model=None, ai_client=fake)
    assert [ref.utterance_id for chapter in result.chapters for ref in chapter.source_refs] == ["utterance-1"]
    assert result.not_story_sources == ()
    assert fake.calls == 2
