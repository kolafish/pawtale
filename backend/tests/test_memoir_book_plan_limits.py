from datetime import datetime
import json

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
from tests.test_memoir_section_organization import FakeStoryGrowthAI


class ReplayPlanAI(FakeStoryGrowthAI):
    def __init__(self, responses):
        super().__init__({})
        self.responses = iter(responses)

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.calls += 1
        response = next(self.responses)
        return json.dumps(response, ensure_ascii=False) if isinstance(response, dict) else response


def _plan_sources(count):
    return [SourceSegment(i, "session", f"message-{i}", f"utterance-{i}", None, None, None, None,
        "小时候奶奶背我赶集。", datetime(2026, 10, 14)) for i in range(1, count + 1)]


TRUNCATED_PLAN = ('{"chapters":[{"title":"未完成",', {"choices": [{"finish_reason": "length"}]})


@pytest.mark.parametrize("count", [32, 128, 256])
@pytest.mark.parametrize("last_truncated", [False, True])
async def test_plan_recovery_retains_best_complete_attempt_within_existing_budget(count, last_truncated):
    from app.services.memoir.book_plan_ai import generate_book_plan_from_sources
    better = {"chapters": [{"title": "童年", "source_indices": list(range(1, count))}]}
    worse = {"chapters": [{"title": "回家", "source_indices": [count]}]}
    fake = ReplayPlanAI([better, TRUNCATED_PLAN if last_truncated else worse])
    result = await generate_book_plan_from_sources(project_title="我的回忆录", sources=_plan_sources(count),
        interviewer_lines={}, ai_model=None, ai_client=fake)
    assert fake.calls == 2
    assert [chapter.title for chapter in result.chapters] == ["童年"]
    assert [ref.utterance_id for chapter in result.chapters for ref in chapter.source_refs] == [f"utterance-{i}" for i in range(1, count)]
    assert result.not_story_sources == ()  # Never union unrelated attempts or classify omitted sources.


@pytest.mark.parametrize("nonstory", [False, True])
async def test_repeated_source_in_same_destination_is_not_a_conflict(nonstory):
    from app.services.memoir.book_plan_ai import generate_book_plan_from_sources
    payload = {"chapters": [], "not_story_source_indices": [1, 1, 2]} if nonstory else {
        "chapters": [{"title": "童年", "source_indices": [1, 1, 2]}]}
    fake = ReplayPlanAI([payload])
    result = await generate_book_plan_from_sources(project_title="我的回忆录", sources=_plan_sources(2),
        interviewer_lines={}, ai_model=None, ai_client=fake)
    assert fake.calls == 1
    assert len(result.chapters) == (0 if nonstory else 1)
    assert len(result.not_story_sources) == (2 if nonstory else 0)
    if not nonstory:
        assert len(result.chapters[0].source_refs) == 2


@pytest.mark.parametrize("responses", [
    [TRUNCATED_PLAN, TRUNCATED_PLAN],
    [{"chapters": [], "not_story_source_indices": [1, 1]}, TRUNCATED_PLAN],
])
async def test_plan_recovery_without_usable_complete_candidate_still_fails(responses):
    from app.services.memoir.book_plan_ai import generate_book_plan_from_sources
    fake = ReplayPlanAI(responses)
    with pytest.raises(HTTPException) as error:
        await generate_book_plan_from_sources(project_title="我的回忆录", sources=_plan_sources(2),
            interviewer_lines={}, ai_model=None, ai_client=fake)
    assert error.value.status_code == 503
    assert fake.calls == 2


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
