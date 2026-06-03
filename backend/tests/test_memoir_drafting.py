import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.domain.memoir import MemoirAnswerStatus, MemoirDraftStatus
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirAnswer
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir import MemoirDraftEditRequest, MemoirDraftGenerateRequest
from app.services.memoir.answers import answer_question_with_text
from app.services.memoir.drafting import (
    accept_draft,
    generate_draft_for_answer,
    update_draft_content,
)
from app.services.memoir.interview import get_or_create_current_session


class FakeAIClient:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, content: str = "整理后的回忆录正文"):
        self.content = content
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
        return self.content

    def _wrap_debug(self, *args, **kwargs):
        return {}


class FailingAIClient(FakeAIClient):
    async def _call_llm(self, *args, **kwargs):
        raise RuntimeError(
            "Server error '503 Service Unavailable' for url "
            "'https://cursor.scihub.edu.kg/api/v1/chat/completions'"
        )


def _user(email: str) -> User:
    return User(email=email, password_hash="hash", display_name=email.split("@")[0])


async def _create_user_project_and_answer(test_db):
    user = _user("memoir-draft@example.com")
    test_db.add(user)
    await test_db.flush()
    project = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title="我的回忆录")
    test_db.add(project)
    await test_db.flush()
    state = await get_or_create_current_session(test_db, project.id, user.id)
    answer = await answer_question_with_text(
        test_db,
        state.current_question.id,
        user.id,
        "我记得外婆家的院子，夏天有一棵很大的树。",
    )
    return user, answer


async def test_generate_draft_for_answer_creates_draft(test_db):
    user, answer = await _create_user_project_and_answer(test_db)
    fake_ai = FakeAIClient("外婆家的院子，是我童年里最清晰的地方。")

    draft = await generate_draft_for_answer(
        test_db,
        answer.id,
        user.id,
        MemoirDraftGenerateRequest(ai_model="deepseek"),
        fake_ai,
    )

    assert draft.status == MemoirDraftStatus.DRAFT
    assert draft.content_raw == "外婆家的院子，是我童年里最清晰的地方。"
    assert draft.content_edited == draft.content_raw
    assert fake_ai.calls
    assert "外婆家的院子" in fake_ai.calls[0]["user_prompt"]


async def test_generate_draft_replaces_existing_draft(test_db):
    user, answer = await _create_user_project_and_answer(test_db)
    first = await generate_draft_for_answer(
        test_db,
        answer.id,
        user.id,
        MemoirDraftGenerateRequest(),
        FakeAIClient("第一版"),
    )
    second = await generate_draft_for_answer(
        test_db,
        answer.id,
        user.id,
        MemoirDraftGenerateRequest(),
        FakeAIClient("第二版"),
    )

    assert first.id == second.id
    assert second.content_raw == "第二版"
    assert second.content_edited == "第二版"


async def test_update_and_accept_draft(test_db):
    user, answer = await _create_user_project_and_answer(test_db)
    draft = await generate_draft_for_answer(
        test_db,
        answer.id,
        user.id,
        MemoirDraftGenerateRequest(),
        FakeAIClient("初稿"),
    )

    edited = await update_draft_content(
        test_db,
        draft.id,
        user.id,
        MemoirDraftEditRequest(content_edited="编辑后的正文"),
    )
    accepted = await accept_draft(test_db, draft.id, user.id)

    assert edited.content_edited == "编辑后的正文"
    assert accepted.status == MemoirDraftStatus.ACCEPTED

    with pytest.raises(HTTPException) as exc:
        await update_draft_content(
            test_db,
            draft.id,
            user.id,
            MemoirDraftEditRequest(content_edited="确认后再编辑"),
        )
    assert exc.value.status_code == 409


async def test_accepting_draft_marks_answer_confirmed(test_db):
    user, answer = await _create_user_project_and_answer(test_db)
    draft = await generate_draft_for_answer(
        test_db,
        answer.id,
        user.id,
        MemoirDraftGenerateRequest(),
        FakeAIClient("确认正文"),
    )

    await accept_draft(test_db, draft.id, user.id)

    with pytest.raises(HTTPException) as exc:
        await generate_draft_for_answer(
            test_db,
            answer.id,
            user.id,
            MemoirDraftGenerateRequest(),
            FakeAIClient("重新生成"),
        )

    assert exc.value.status_code == 409
    saved_answer = await test_db.scalar(select(MemoirAnswer).where(MemoirAnswer.id == answer.id))
    assert saved_answer.status == MemoirAnswerStatus.CONFIRMED.value


async def test_draft_generation_rejects_empty_answer(test_db):
    user = _user("empty-answer@example.com")
    test_db.add(user)
    await test_db.flush()
    project = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title="我的回忆录")
    test_db.add(project)
    await test_db.flush()
    state = await get_or_create_current_session(test_db, project.id, user.id)
    answer = await answer_question_with_text(test_db, state.current_question.id, user.id, "临时回答")
    saved_answer = await test_db.scalar(select(MemoirAnswer).where(MemoirAnswer.id == answer.id))
    saved_answer.transcript_raw = ""
    saved_answer.transcript_edited = ""
    await test_db.commit()

    with pytest.raises(HTTPException) as exc:
        await generate_draft_for_answer(
            test_db,
            answer.id,
            user.id,
            MemoirDraftGenerateRequest(),
            FakeAIClient(),
        )

    assert exc.value.status_code == 400


async def test_ai_failure_does_not_change_answer_status(test_db):
    user, answer = await _create_user_project_and_answer(test_db)

    with pytest.raises(HTTPException) as exc:
        await generate_draft_for_answer(
            test_db,
            answer.id,
            user.id,
            MemoirDraftGenerateRequest(),
            FailingAIClient(),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "AI 服务暂时不可用，请稍后重试。"
    assert "cursor.scihub" not in str(exc.value.detail)
    saved_answer = await test_db.scalar(select(MemoirAnswer).where(MemoirAnswer.id == answer.id))
    assert saved_answer.status == MemoirAnswerStatus.ANSWERED.value
