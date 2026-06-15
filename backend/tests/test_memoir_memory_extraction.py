import json

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.domain.memoir import MemoirMemoryType, MemoirOutputType, MemoirQuestionType
from app.domain.projects.types import ProjectType
from app.models.memoir import (
    MemoirAnswer,
    MemoirEventMemory,
    MemoirMemoryEvidence,
    MemoirOpenQuestion,
    MemoirOutput,
    MemoirPersonMemory,
    MemoirPlaceMemory,
    MemoirQuestion,
    MemoirSessionSummary,
    MemoirThemeMemory,
)
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir import MemoirSessionProcessRequest
from app.services.memoir.answers import answer_question_with_text
from app.services.memoir.interview import get_or_create_current_session
from app.services.memoir.memory_extraction import process_session_memory


class FakeExtractionAIClient:
    provider_name = "fake"
    model_name = "fake-extractor"

    def __init__(self, payload: dict | str):
        self.payload = payload
        self.calls: list[dict] = []

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        return_raw: bool = False,
    ):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "return_raw": return_raw,
        })
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)

    def _wrap_debug(self, *args, **kwargs):
        return {}


class FailingExtractionAIClient(FakeExtractionAIClient):
    async def _call_llm(self, *args, **kwargs):
        raise RuntimeError("upstream unavailable")


def _user(email: str) -> User:
    return User(email=email, password_hash="hash", display_name=email.split("@")[0])


async def _create_project(test_db, user: User) -> Project:
    project = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title="我的回忆录")
    test_db.add(project)
    await test_db.flush()
    return project


async def _create_answered_session(test_db) -> tuple[User, Project, str, list[str]]:
    user = _user("extractor@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    first_question_id = state.current_question.id
    first = await answer_question_with_text(
        test_db,
        first_question_id,
        user.id,
        "我第一次去重庆厂里上班，是老张带我去的。",
    )
    next_state = await get_or_create_current_session(test_db, project.id, user.id)
    second = await answer_question_with_text(
        test_db,
        next_state.current_question.id,
        user.id,
        "那个厂宿舍很挤，但我现在想起来还是有点骄傲。",
    )
    return user, project, state.session.id, [first.id, second.id]


def _payload(answer_id: str) -> dict:
    return {
        "people": [
            {
                "canonical_name": "老张",
                "aliases": ["张师傅"],
                "relationship": "同事",
                "description": "第一次去重庆厂里时带受访者去的人",
                "confidence": 0.86,
                "evidence_quote": "老张带我去的",
                "answer_id": answer_id,
            }
        ],
        "events": [
            {
                "title": "第一次去重庆工作",
                "life_stage": "青年",
                "time_text": "第一次去重庆厂里上班",
                "place_text": "重庆厂",
                "process": "老张带受访者去重庆厂里上班",
                "emotion": "有点骄傲",
                "importance": 0.8,
                "confidence": 0.9,
                "evidence_quote": "第一次去重庆厂里上班",
                "answer_id": answer_id,
            }
        ],
        "places": [
            {
                "name": "重庆厂",
                "elder_alias": "那个厂",
                "place_type": "工作地点",
                "description": "第一次工作的地方",
                "confidence": 0.8,
                "evidence_quote": "重庆厂里上班",
                "answer_id": answer_id,
            }
        ],
        "themes": [
            {
                "name": "骄傲",
                "description": "回望艰苦工作经历时仍有价值感",
                "strength": 0.7,
                "mention_count": 1,
                "confidence": 0.75,
                "evidence_quote": "还是有点骄傲",
            }
        ],
        "open_questions": [
            {
                "question": "你第一次进重庆厂那天，宿舍具体是什么样的？",
                "question_type": "scene_detail",
                "reason": "关键事件缺少场景细节",
                "sensitivity": "low",
                "priority": 0.82,
                "target_memory_type": "event",
                "target_title": "第一次去重庆工作",
                "evidence_quote": "那个厂宿舍很挤",
            }
        ],
        "session_summary": "本次访谈提到第一次去重庆厂里工作、老张和厂宿舍。",
        "story_title": "第一次去重庆工作",
        "story_fragment": "第一次去重庆厂里上班，是老张带我去的。宿舍很挤，但多年后回想，仍有一点骄傲。",
        "next_question_suggestions": [{"question": "那天是谁送你出门的？"}],
    }


async def _count(test_db, model) -> int:
    return await test_db.scalar(select(func.count()).select_from(model))


async def test_process_session_memory_extracts_memories_summary_and_output(test_db):
    user, project, session_id, answer_ids = await _create_answered_session(test_db)
    fake_ai = FakeExtractionAIClient(_payload(answer_ids[0]))

    result = await process_session_memory(
        test_db,
        session_id,
        user.id,
        MemoirSessionProcessRequest(ai_model="deepseek"),
        fake_ai,
    )

    assert fake_ai.calls
    call = fake_ai.calls[0]
    assert "严格 JSON" in call["user_prompt"]
    assert "第一次去重庆厂里上班" in call["user_prompt"]
    assert call["temperature"] == 0.2
    assert len(result.people) == 1
    assert result.people[0].canonical_name == "老张"
    assert len(result.events) == 1
    assert result.events[0].title == "第一次去重庆工作"
    assert len(result.places) == 1
    assert len(result.themes) == 1
    assert result.open_questions[0].question_type == MemoirQuestionType.SCENE
    assert result.open_questions[0].target_memory_type == MemoirMemoryType.EVENT.value
    assert result.session_summary.summary == "本次访谈提到第一次去重庆厂里工作、老张和厂宿舍。"
    assert result.session_summary.new_people_json[0]["canonical_name"] == "老张"
    assert result.session_summary.next_question_suggestions_json == [
        {"question": "那天是谁送你出门的？"}
    ]
    assert result.story_fragment is not None
    assert result.story_fragment.output_type == MemoirOutputType.STORY_FRAGMENT

    evidence_rows = (
        await test_db.execute(
            select(MemoirMemoryEvidence).where(MemoirMemoryEvidence.project_id == project.id)
        )
    ).scalars().all()
    assert len(evidence_rows) == 5
    assert {row.memory_type for row in evidence_rows} == {
        "person",
        "event",
        "place",
        "theme",
        "open_question",
    }
    assert all(row.session_id == session_id for row in evidence_rows)

    output = await test_db.scalar(
        select(MemoirOutput)
        .where(MemoirOutput.source_session_id == session_id)
        .options(selectinload(MemoirOutput.versions))
    )
    assert output is not None
    assert output.versions[0].content.startswith("第一次去重庆厂里上班")
    assert output.versions[0].model == "fake-extractor"


async def test_process_session_memory_reuses_existing_extraction_rows(test_db):
    user, _, session_id, answer_ids = await _create_answered_session(test_db)
    fake_ai = FakeExtractionAIClient(_payload(answer_ids[0]))

    first = await process_session_memory(
        test_db,
        session_id,
        user.id,
        MemoirSessionProcessRequest(),
        fake_ai,
    )
    second = await process_session_memory(
        test_db,
        session_id,
        user.id,
        MemoirSessionProcessRequest(),
        fake_ai,
    )

    assert first.people[0].id == second.people[0].id
    assert first.events[0].id == second.events[0].id
    assert first.places[0].id == second.places[0].id
    assert first.themes[0].id == second.themes[0].id
    assert first.open_questions[0].id == second.open_questions[0].id
    assert await _count(test_db, MemoirPersonMemory) == 1
    assert await _count(test_db, MemoirEventMemory) == 1
    assert await _count(test_db, MemoirPlaceMemory) == 1
    assert await _count(test_db, MemoirThemeMemory) == 1
    assert await _count(test_db, MemoirOpenQuestion) == 1
    assert await _count(test_db, MemoirMemoryEvidence) == 5


async def test_process_session_memory_rejects_empty_session_without_ai_call(test_db):
    user = _user("empty-extractor@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    fake_ai = FakeExtractionAIClient({})

    with pytest.raises(HTTPException) as exc:
        await process_session_memory(
            test_db,
            state.session.id,
            user.id,
            MemoirSessionProcessRequest(),
            fake_ai,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "本次访谈没有可处理的回答"
    assert fake_ai.calls == []


async def test_process_session_memory_rejects_invalid_json_without_writes(test_db):
    user, _, session_id, _ = await _create_answered_session(test_db)

    with pytest.raises(HTTPException) as exc:
        await process_session_memory(
            test_db,
            session_id,
            user.id,
            MemoirSessionProcessRequest(),
            FakeExtractionAIClient("not json"),
        )

    assert exc.value.status_code == 502
    assert await _count(test_db, MemoirPersonMemory) == 0
    assert await _count(test_db, MemoirEventMemory) == 0
    assert await _count(test_db, MemoirSessionSummary) == 0


async def test_process_session_memory_ai_failure_does_not_change_answers(test_db):
    user, _, session_id, answer_ids = await _create_answered_session(test_db)

    with pytest.raises(HTTPException) as exc:
        await process_session_memory(
            test_db,
            session_id,
            user.id,
            MemoirSessionProcessRequest(),
            FailingExtractionAIClient({}),
        )

    saved_answer = await test_db.scalar(select(MemoirAnswer).where(MemoirAnswer.id == answer_ids[0]))
    assert exc.value.status_code == 503
    assert saved_answer.transcript_edited == "我第一次去重庆厂里上班，是老张带我去的。"
    assert await _count(test_db, MemoirSessionSummary) == 0
