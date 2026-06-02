import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.domain.memoir import (
    MemoirAnswerStatus,
    MemoirDraftStatus,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirSessionStatus,
)
from app.domain.projects.types import ProjectType
from app.models.memoir import AudioFile, MemoirAnswer, MemoirDraft, MemoirQuestion, MemoirSession
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir import (
    MemoirAnswerResponse,
    MemoirDraftResponse,
    MemoirQuestionResponse,
    MemoirSessionResponse,
)


def _user(email: str) -> User:
    return User(email=email, password_hash="hash", display_name=email.split("@")[0])


async def _create_memoir_project(test_db, user: User, title: str = "我的回忆录") -> Project:
    project = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title=title)
    test_db.add(project)
    await test_db.flush()
    return project


async def _count(test_db, model) -> int:
    return await test_db.scalar(select(func.count()).select_from(model))


async def test_memoir_interview_models_round_trip(test_db):
    user = _user("memoir@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_memoir_project(test_db, user)

    session = MemoirSession(project_id=project.id, title="童年")
    question = MemoirQuestion(
        session=session,
        prompt="你小时候最常去的地方是哪里？",
        sort_order=0,
    )
    answer = MemoirAnswer(
        question=question,
        audio_file=AudioFile(
            user_id=user.id,
            storage_path="memoir/audio/answer.m4a",
            mime_type="audio/mp4",
            size_bytes=1024,
            duration_seconds=12,
        ),
        transcript_raw="外婆家的院子。",
        transcript_edited="我小时候最常去外婆家的院子。",
        duration_seconds=12,
    )
    MemoirDraft(
        answer=answer,
        content_raw="外婆家的院子是童年里很清晰的地方。",
        content_edited="外婆家的院子，是我童年里最清晰的地方。",
    )

    test_db.add(session)
    await test_db.flush()
    session.current_question_id = question.id
    await test_db.commit()

    loaded = await test_db.scalar(
        select(MemoirSession)
        .where(MemoirSession.id == session.id)
        .options(
            selectinload(MemoirSession.questions)
            .selectinload(MemoirQuestion.answer)
            .selectinload(MemoirAnswer.draft),
            selectinload(MemoirSession.questions)
            .selectinload(MemoirQuestion.answer)
            .selectinload(MemoirAnswer.audio_file),
        )
    )

    assert loaded is not None
    assert loaded.status == MemoirSessionStatus.ACTIVE.value
    assert loaded.current_question_id == loaded.questions[0].id
    assert loaded.questions[0].source == MemoirQuestionSource.TEMPLATE.value
    assert loaded.questions[0].status == MemoirQuestionStatus.PENDING.value
    assert loaded.questions[0].answer.status == MemoirAnswerStatus.ANSWERED.value
    assert loaded.questions[0].answer.audio_file.storage_path == "memoir/audio/answer.m4a"
    assert loaded.questions[0].answer.draft.status == MemoirDraftStatus.DRAFT.value

    session_response = MemoirSessionResponse.model_validate(loaded)
    question_response = MemoirQuestionResponse.model_validate(loaded.questions[0])
    answer_response = MemoirAnswerResponse.model_validate(loaded.questions[0].answer)
    draft_response = MemoirDraftResponse.model_validate(loaded.questions[0].answer.draft)

    assert session_response.status == MemoirSessionStatus.ACTIVE
    assert question_response.source == MemoirQuestionSource.TEMPLATE
    assert answer_response.status == MemoirAnswerStatus.ANSWERED
    assert draft_response.status == MemoirDraftStatus.DRAFT


async def test_deleting_project_cascades_memoir_interview_data(test_db):
    user = _user("cascade@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_memoir_project(test_db, user)

    session = MemoirSession(project=project, title="父母")
    question = MemoirQuestion(session=session, prompt="你最早记得父母的一件事是什么？")
    answer = MemoirAnswer(
        question=question,
        audio_file=AudioFile(
            user_id=user.id,
            storage_path="memoir/audio/parents.m4a",
            mime_type="audio/mp4",
            size_bytes=2048,
        ),
        transcript_edited="一段回答",
    )
    MemoirDraft(answer=answer, content_edited="整理后的正文")
    test_db.add(session)
    await test_db.commit()

    await test_db.delete(project)
    await test_db.commit()

    assert await _count(test_db, MemoirSession) == 0
    assert await _count(test_db, MemoirQuestion) == 0
    assert await _count(test_db, MemoirAnswer) == 0
    assert await _count(test_db, MemoirDraft) == 0
    assert await _count(test_db, AudioFile) == 0


async def test_memoir_sessions_remain_queryable_by_project_owner(test_db):
    user_a = _user("owner-a@example.com")
    user_b = _user("owner-b@example.com")
    test_db.add_all([user_a, user_b])
    await test_db.flush()
    project_a = await _create_memoir_project(test_db, user_a, "A 的回忆录")
    project_b = await _create_memoir_project(test_db, user_b, "B 的回忆录")
    test_db.add_all([
        MemoirSession(project_id=project_a.id, title="A 访谈"),
        MemoirSession(project_id=project_b.id, title="B 访谈"),
    ])
    await test_db.commit()

    result = await test_db.execute(
        select(MemoirSession.title)
        .join(Project)
        .where(Project.user_id == user_a.id)
    )

    assert result.scalars().all() == ["A 访谈"]


async def test_memoir_status_constraints_reject_unknown_values(test_db):
    user = _user("constraint@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_memoir_project(test_db, user)
    session = MemoirSession(project_id=project.id, title="状态测试")
    test_db.add(session)
    await test_db.flush()

    test_db.add(MemoirQuestion(session_id=session.id, prompt="问题", status="unknown"))

    with pytest.raises(IntegrityError):
        await test_db.flush()
    await test_db.rollback()
