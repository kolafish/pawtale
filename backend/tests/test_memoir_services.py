import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.domain.memoir import MemoirAnswerStatus, MemoirQuestionStatus, MemoirSessionStatus
from app.domain.projects.types import ProjectType
from app.models.memoir import AudioFile, MemoirAnswer, MemoirQuestion
from app.models.projects import Project
from app.models.users import User
from app.services.memoir.answers import answer_question_with_text, update_answer_text
from app.services.memoir.interview import (
    get_next_question,
    get_or_create_current_session,
    get_session_state,
    skip_question,
)


def _user(email: str) -> User:
    return User(email=email, password_hash="hash", display_name=email.split("@")[0])


async def _create_project(
    test_db,
    user: User,
    project_type: ProjectType = ProjectType.MEMOIR,
) -> Project:
    project = Project(user_id=user.id, type=project_type.value, title="测试项目")
    test_db.add(project)
    await test_db.flush()
    return project


async def _create_user_and_project(test_db) -> tuple[User, Project]:
    user = _user("memoir-service@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)
    return user, project


async def _count(test_db, model) -> int:
    return await test_db.scalar(select(func.count()).select_from(model))


async def test_get_or_create_current_session_initializes_template_questions(test_db):
    user, project = await _create_user_and_project(test_db)

    state = await get_or_create_current_session(test_db, project.id, user.id)
    same_state = await get_or_create_current_session(test_db, project.id, user.id)
    next_question = await get_next_question(test_db, project.id, user.id)

    assert state.session.id == same_state.session.id
    assert state.session.status == MemoirSessionStatus.ACTIVE
    assert state.session.title == "回忆录访谈"
    assert state.current_question is not None
    assert state.current_question.id == state.session.current_question_id
    assert next_question.id == state.current_question.id
    assert state.pending_question_count == 5
    assert state.completed_question_count == 0
    assert len(state.questions) == 5
    assert await _count(test_db, MemoirQuestion) == 5


async def test_text_answer_creates_answer_and_advances_question(test_db):
    user, project = await _create_user_and_project(test_db)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    first_question_id = state.current_question.id

    answer = await answer_question_with_text(test_db, first_question_id, user.id, "  我记得外婆家。 ")
    next_state = await get_session_state(test_db, state.session.id, user.id)

    assert answer.status == MemoirAnswerStatus.ANSWERED
    assert answer.transcript_raw == "我记得外婆家。"
    assert answer.transcript_edited == "我记得外婆家。"
    assert next_state.questions[0].status == MemoirQuestionStatus.ANSWERED
    assert next_state.current_question is not None
    assert next_state.current_question.id == next_state.questions[1].id
    assert next_state.completed_question_count == 1
    assert next_state.pending_question_count == 4


async def test_text_answer_links_owned_audio_file(test_db):
    user, project = await _create_user_and_project(test_db)
    audio_file = AudioFile(
        user_id=user.id,
        storage_path=f"memoir/audio/{user.id}/answer.mp3",
        mime_type="audio/mpeg",
        size_bytes=123,
        duration_seconds=9,
    )
    test_db.add(audio_file)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    await test_db.flush()

    answer = await answer_question_with_text(
        test_db,
        state.current_question.id,
        user.id,
        "我小时候经常去外婆家。",
        audio_file_id=audio_file.id,
    )

    assert answer.audio_file_id == audio_file.id
    assert answer.duration_seconds == 9
    stored = await test_db.scalar(select(MemoirAnswer).where(MemoirAnswer.id == answer.id))
    assert stored.audio_file_id == audio_file.id
    assert stored.duration_seconds == 9


async def test_text_answer_rejects_other_users_audio_file(test_db):
    owner, project = await _create_user_and_project(test_db)
    other = _user("memoir-audio-owner@example.com")
    test_db.add(other)
    await test_db.flush()
    other_audio = AudioFile(
        user_id=other.id,
        storage_path=f"memoir/audio/{other.id}/answer.mp3",
        mime_type="audio/mpeg",
        size_bytes=123,
        duration_seconds=9,
    )
    test_db.add(other_audio)
    state = await get_or_create_current_session(test_db, project.id, owner.id)
    await test_db.flush()

    with pytest.raises(HTTPException) as exc:
        await answer_question_with_text(
            test_db,
            state.current_question.id,
            owner.id,
            "我小时候经常去外婆家。",
            audio_file_id=other_audio.id,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "音频文件不存在"


async def test_answering_existing_question_updates_single_answer(test_db):
    user, project = await _create_user_and_project(test_db)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    question_id = state.current_question.id

    first_answer = await answer_question_with_text(test_db, question_id, user.id, "第一次回答")
    second_answer = await answer_question_with_text(test_db, question_id, user.id, "第二次回答")

    assert first_answer.id == second_answer.id
    assert second_answer.transcript_raw == "第一次回答"
    assert second_answer.transcript_edited == "第二次回答"
    assert await _count(test_db, MemoirAnswer) == 1


async def test_update_answer_text_preserves_raw_transcript(test_db):
    user, project = await _create_user_and_project(test_db)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    answer = await answer_question_with_text(test_db, state.current_question.id, user.id, "原始回答")

    updated = await update_answer_text(test_db, answer.id, user.id, " 修改后的回答 ")

    assert updated.transcript_raw == "原始回答"
    assert updated.transcript_edited == "修改后的回答"
    assert updated.status == MemoirAnswerStatus.ANSWERED


async def test_skip_question_advances_and_completes_session(test_db):
    user, project = await _create_user_and_project(test_db)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    question_ids = [question.id for question in state.questions]

    for question_id in question_ids:
        state = await skip_question(test_db, question_id, user.id)

    assert state.session.status == MemoirSessionStatus.COMPLETED
    assert state.current_question is None
    assert state.completed_question_count == 5
    assert state.pending_question_count == 0
    assert [question.status for question in state.questions] == [
        MemoirQuestionStatus.SKIPPED,
    ] * 5


async def test_answered_question_cannot_be_skipped(test_db):
    user, project = await _create_user_and_project(test_db)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    question_id = state.current_question.id
    await answer_question_with_text(test_db, question_id, user.id, "已经回答")

    with pytest.raises(HTTPException) as exc:
        await skip_question(test_db, question_id, user.id)

    assert exc.value.status_code == 409


async def test_text_answer_rejects_blank_content(test_db):
    user, project = await _create_user_and_project(test_db)
    state = await get_or_create_current_session(test_db, project.id, user.id)

    with pytest.raises(HTTPException) as exc:
        await answer_question_with_text(test_db, state.current_question.id, user.id, "  ")

    assert exc.value.status_code == 400
    assert exc.value.detail == "回答不能为空"


async def test_memoir_services_reject_non_memoir_project(test_db):
    user = _user("article-owner@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user, ProjectType.ARTICLE)

    with pytest.raises(HTTPException) as exc:
        await get_or_create_current_session(test_db, project.id, user.id)

    assert exc.value.status_code == 400
    assert exc.value.detail == "项目不是回忆录"


async def test_memoir_services_hide_other_users_questions_and_answers(test_db):
    owner, project = await _create_user_and_project(test_db)
    other = _user("other-user@example.com")
    test_db.add(other)
    await test_db.flush()
    state = await get_or_create_current_session(test_db, project.id, owner.id)
    answer = await answer_question_with_text(test_db, state.current_question.id, owner.id, "我的回答")

    with pytest.raises(HTTPException) as question_exc:
        await answer_question_with_text(test_db, state.questions[1].id, other.id, "越权回答")
    with pytest.raises(HTTPException) as answer_exc:
        await update_answer_text(test_db, answer.id, other.id, "越权编辑")

    assert question_exc.value.status_code == 404
    assert answer_exc.value.status_code == 404
