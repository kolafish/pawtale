from __future__ import annotations

import json

from sqlalchemy import func, select

from app.domain.memoir.types import MemoirBookPlanStatus, MemoirCreatedBy, MemoirGuidanceMode, MemoirSemanticCacheStatus
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterCard,
    MemoirChapterSourceLink,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from scripts.backfill_memoir_chapter_cards import run_chapter_card_backfill
from scripts.backfill_memoir_chapter_source_links import run_chapter_source_link_backfill
from scripts.backfill_memoir_source_cards import run_source_card_backfill
from scripts.inspect_memoir_semantic_state import run_semantic_state_inspection


class FakeSourceCardAI:
    provider_name = "fake"
    model_name = "fake-source"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "kwargs": kwargs})
        return json.dumps(
            {
                "short_title": "外婆家的厨房",
                "summary": "讲述者回忆小时候在外婆家厨房帮忙。",
                "people": ["外婆"],
                "places": ["外婆家厨房"],
                "time_hints": ["小时候"],
                "event_tags": ["做饭"],
                "emotion_tags": ["怀念"],
                "key_facts": [
                    {"text": "小时候常在外婆家厨房帮忙", "evidence_quote": "小时候常在外婆家厨房帮忙"}
                ],
                "open_questions": ["厨房是什么样子？"],
            },
            ensure_ascii=False,
        )


class FakeChapterCardAI:
    provider_name = "fake"
    model_name = "fake-chapter"

    def __init__(self, utterance_id: str) -> None:
        self.utterance_id = utterance_id
        self.calls: list[dict[str, object]] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "kwargs": kwargs})
        return json.dumps(
            {
                "summary": "本章围绕外婆家的童年生活。",
                "covered": {
                    "time": ["小时候"],
                    "places": ["外婆家厨房"],
                    "people": ["外婆"],
                    "events": ["做饭"],
                },
                "gaps": [],
                "next_questions": ["厨房是什么样子？"],
                "evidence_refs": [
                    {"utterance_id": self.utterance_id, "quote": "小时候常在外婆家厨房帮忙"}
                ],
            },
            ensure_ascii=False,
        )


async def _create_project_with_chapter(test_db):
    project = Project(title="回忆录项目", type="memoir")
    test_db.add(project)
    await test_db.flush()
    voice_session = MemoirVoiceSession(
        project_id=project.id,
        status="created",
        guidance_mode=MemoirGuidanceMode.FREE_NARRATION.value,
    )
    test_db.add(voice_session)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        transcript_raw="小时候常在外婆家厨房帮忙，我看见外婆用大铁锅做饭。",
        transcript_edited="小时候常在外婆家厨房帮忙，我看见外婆用大铁锅做饭。",
    )
    test_db.add(utterance)
    await test_db.flush()
    plan = MemoirBookPlan(
        project_id=project.id,
        title="童年",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        project_id=project.id,
        plan_id=plan.id,
        title="外婆家的厨房",
        sort_order=0,
        source_refs_json=[
            {
                "voice_session_id": voice_session.id,
                "message_id": "message-1",
                "utterance_id": utterance.id,
                "transcript_version_id": None,
                "audio_file_id": None,
                "start_ms": None,
                "end_ms": None,
                "quote": "小时候常在外婆家厨房帮忙",
                "created_at": utterance.created_at.isoformat(),
            }
        ],
    )
    test_db.add(chapter)
    await test_db.commit()
    return project, chapter, utterance


async def test_chapter_source_link_backfill_dry_run_and_execute(test_db):
    project, _, _ = await _create_project_with_chapter(test_db)
    project_id = project.id

    dry_run = await run_chapter_source_link_backfill(test_db, project_id=project_id, dry_run=True)
    assert dry_run["created_count"] == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirChapterSourceLink)) == 0

    executed = await run_chapter_source_link_backfill(test_db, project_id=project_id, dry_run=False)
    assert executed["created_count"] == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirChapterSourceLink)) == 1

    second = await run_chapter_source_link_backfill(test_db, project_id=project_id, dry_run=False)
    assert second["created_count"] == 0
    assert second["failure_count"] == 0


async def test_source_card_backfill_dry_run_does_not_call_ai_or_write(test_db):
    project, _, _ = await _create_project_with_chapter(test_db)
    project_id = project.id
    fake_ai = FakeSourceCardAI()

    result = await run_source_card_backfill(
        test_db,
        project_id=project_id,
        dry_run=True,
        ai_client=fake_ai,
    )

    assert result["candidate_count"] == 1
    assert result["ready_count"] == 0
    assert fake_ai.calls == []
    assert await test_db.scalar(select(func.count()).select_from(MemoirSourceCard)) == 0


async def test_source_and_chapter_card_backfills_execute_with_stats(test_db):
    project, _, utterance = await _create_project_with_chapter(test_db)
    project_id = project.id
    utterance_id = utterance.id
    source_ai = FakeSourceCardAI()
    chapter_ai = FakeChapterCardAI(utterance_id)

    source_result = await run_source_card_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
        ai_client=source_ai,
    )
    chapter_dry_run = await run_chapter_card_backfill(
        test_db,
        project_id=project_id,
        dry_run=True,
        ai_client=chapter_ai,
    )
    chapter_result = await run_chapter_card_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
        ai_client=chapter_ai,
    )

    assert source_result["ready_count"] == 1
    assert source_result["failed_count"] == 0
    assert chapter_dry_run["candidate_count"] == 1
    assert chapter_dry_run["ready_count"] == 0
    assert chapter_result["ready_count"] == 1
    assert chapter_result["failed_count"] == 0
    assert len(source_ai.calls) == 1
    assert len(chapter_ai.calls) == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirSourceCard)) == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirChapterCard)) == 1

    inspection = await run_semantic_state_inspection(test_db, project_id=project_id)
    project_state = inspection["projects"][0]
    assert project_state["source_cards"]["ready"] == 1
    assert project_state["source_cards"]["missing_current_ready"] == 0
    assert project_state["chapter_cards"]["ready"] == 1
    assert project_state["chapter_cards"]["missing_current_ready"] == 0
    assert project_state["failure_count"] == 0


async def test_inspection_reports_missing_cards(test_db):
    project, _, _ = await _create_project_with_chapter(test_db)
    project_id = project.id

    inspection = await run_semantic_state_inspection(test_db, project_id=project_id)

    project_state = inspection["projects"][0]
    assert project_state["source_cards"]["missing_current_ready"] == 1
    assert project_state["chapter_cards"]["missing_current_ready"] == 1
    assert project_state["chapter_sources"]["legacy_source_ref_count"] == 1
    assert project_state["chapter_sources"]["legacy_refs_without_any_link_count"] == 1
