from sqlalchemy import select

from app.domain.memoir import (
    MemoirMemoryType,
    MemoirOpenQuestionStatus,
    MemoirQuestionCandidateStatus,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirQuestionType,
    MemoirSensitivity,
)
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirOpenQuestion, MemoirQuestion, MemoirQuestionCandidate
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir import MemoirEventMemoryCreate, MemoirOpenQuestionCreate
from app.services.memoir.answers import answer_question_with_text
from app.services.memoir.interview import get_next_question, get_or_create_current_session
from app.services.memoir.memory_repository import create_event_memory, create_open_question
from app.services.memoir.question_planner import (
    best_question_candidate,
    generate_question_candidates,
    select_candidate_as_question,
)


def _user(email: str) -> User:
    return User(email=email, password_hash="hash", display_name=email.split("@")[0])


async def _create_project(test_db, user: User) -> Project:
    project = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title="我的回忆录")
    test_db.add(project)
    await test_db.flush()
    return project


async def _seed_planner_context(test_db):
    user = _user("planner@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    await answer_question_with_text(
        test_db,
        state.current_question.id,
        user.id,
        "我第一次去重庆厂里上班，后来想起还是有点骄傲。",
    )
    event = await create_event_memory(
        test_db,
        user.id,
        MemoirEventMemoryCreate(
            project_id=project.id,
            title="第一次去重庆工作",
            life_stage="青年",
            importance=0.9,
            confidence=0.8,
        ),
    )
    open_question = await create_open_question(
        test_db,
        user.id,
        MemoirOpenQuestionCreate(
            project_id=project.id,
            session_id=state.session.id,
            target_memory_type=MemoirMemoryType.EVENT,
            target_memory_id=event.id,
            question="第一次去重庆厂里那天，是谁送你去的？",
            question_type=MemoirQuestionType.SCENE,
            reason="关键事件缺少场景人物",
            sensitivity=MemoirSensitivity.LOW,
            priority=0.82,
        ),
    )
    await create_open_question(
        test_db,
        user.id,
        MemoirOpenQuestionCreate(
            project_id=project.id,
            session_id=state.session.id,
            target_memory_type=MemoirMemoryType.EVENT,
            target_memory_id=event.id,
            question="这件事里面有没有让你很难过、不想再提的部分？",
            question_type=MemoirQuestionType.EMOTION,
            reason="情绪可能较敏感，需要谨慎处理",
            sensitivity=MemoirSensitivity.HIGH,
            priority=0.99,
        ),
    )
    return user, project, state.session.id, event.id, open_question.id


async def test_generate_question_candidates_from_open_questions_event_gaps_and_coverage(test_db):
    user, project, session_id, event_id, _ = await _seed_planner_context(test_db)

    candidates = await generate_question_candidates(test_db, user.id, project.id, session_id)
    prompts = {candidate.question for candidate in candidates}

    assert "第一次去重庆厂里那天，是谁送你去的？" in prompts
    assert f"你刚才说的「第一次去重庆工作」，大概是在什么时候？" in prompts
    assert f"「第一次去重庆工作」这件事主要发生在哪里？" in prompts
    assert any("覆盖度较低" in candidate.reason for candidate in candidates)
    event_gap = next(candidate for candidate in candidates if "大概是在什么时候" in candidate.question)
    assert event_gap.target_memory_type == MemoirMemoryType.EVENT.value
    assert event_gap.target_memory_id == event_id


async def test_best_question_candidate_prefers_non_sensitive_specific_gap(test_db):
    user, project, session_id, _, _ = await _seed_planner_context(test_db)

    candidate = await best_question_candidate(test_db, user.id, project.id, session_id)

    assert candidate is not None
    assert candidate.question == "第一次去重庆厂里那天，是谁送你去的？"
    assert candidate.sensitivity == MemoirSensitivity.LOW.value


async def test_select_candidate_as_question_marks_open_question_asked(test_db):
    user, project, session_id, _, open_question_id = await _seed_planner_context(test_db)
    await generate_question_candidates(test_db, user.id, project.id, session_id)
    candidate = await test_db.scalar(
        select(MemoirQuestionCandidate).where(
            MemoirQuestionCandidate.question == "第一次去重庆厂里那天，是谁送你去的？"
        )
    )

    question = await select_candidate_as_question(test_db, user.id, candidate.id)
    saved_open_question = await test_db.scalar(
        select(MemoirOpenQuestion).where(MemoirOpenQuestion.id == open_question_id)
    )
    saved_candidate = await test_db.scalar(
        select(MemoirQuestionCandidate).where(MemoirQuestionCandidate.id == candidate.id)
    )
    saved_question = await test_db.scalar(select(MemoirQuestion).where(MemoirQuestion.id == question.id))

    assert question.prompt == "第一次去重庆厂里那天，是谁送你去的？"
    assert saved_question.source == MemoirQuestionSource.AI_GENERATED.value
    assert saved_question.status == MemoirQuestionStatus.PENDING.value
    assert saved_candidate.status == MemoirQuestionCandidateStatus.SELECTED.value
    assert saved_open_question.status == MemoirOpenQuestionStatus.ASKED.value


async def test_get_next_question_uses_planner_before_template_fallback(test_db):
    user, project, _, _, _ = await _seed_planner_context(test_db)

    next_question = await get_next_question(test_db, project.id, user.id)

    assert next_question.prompt == "第一次去重庆厂里那天，是谁送你去的？"
    saved_question = await test_db.scalar(
        select(MemoirQuestion).where(MemoirQuestion.id == next_question.id)
    )
    assert saved_question.source == MemoirQuestionSource.AI_GENERATED.value
