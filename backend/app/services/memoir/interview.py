from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir import MemoirQuestionStatus, MemoirSessionStatus
from app.models.memoir import MemoirAnswer, MemoirQuestion, MemoirSession
from app.schemas.memoir import (
    MemoirDraftResponse,
    MemoirInterviewState,
    MemoirQuestionResponse,
    MemoirSessionCreate,
    MemoirSessionResponse,
)
from app.services.memoir.access import load_memoir_project, load_question, load_session
from app.services.memoir.question_planner import get_or_create_planned_question


DEFAULT_SESSION_TITLE = "回忆录访谈"

DEFAULT_MEMOIR_QUESTIONS = (
    "你小时候最常去的一个地方是哪里？那里有什么声音、气味，或者你现在还记得的细节？",
    "你最早清楚记得的人是谁？当时你们之间发生了什么？",
    "有没有一件事让你觉得自己长大了，或者开始用不同的方式看待世界？",
    "你生命里有没有一次重要的选择？当时你为什么那样决定？",
    "现在回头看，你最想把哪段经历留给家人或未来的自己？",
)


def _ordered_questions(session: MemoirSession) -> list[MemoirQuestion]:
    return sorted(session.questions, key=lambda q: q.sort_order)


def _advance_loaded_session(session: MemoirSession) -> MemoirQuestion | None:
    next_question = next(
        (
            q for q in _ordered_questions(session)
            if q.status == MemoirQuestionStatus.PENDING.value
        ),
        None,
    )
    session.current_question_id = next_question.id if next_question else None
    session.status = (
        MemoirSessionStatus.ACTIVE.value
        if next_question
        else MemoirSessionStatus.COMPLETED.value
    )
    return next_question


def _session_state(session: MemoirSession) -> MemoirInterviewState:
    questions = _ordered_questions(session)
    current_question = next(
        (q for q in questions if q.id == session.current_question_id),
        None,
    )
    latest_draft = max(
        (
            q.answer.draft
            for q in questions
            if q.answer and q.answer.draft
        ),
        key=lambda draft: draft.updated_at,
        default=None,
    )
    completed_statuses = {
        MemoirQuestionStatus.ANSWERED.value,
        MemoirQuestionStatus.SKIPPED.value,
    }

    return MemoirInterviewState(
        session=MemoirSessionResponse.model_validate(session),
        current_question=(
            MemoirQuestionResponse.model_validate(current_question)
            if current_question
            else None
        ),
        completed_question_count=sum(q.status in completed_statuses for q in questions),
        pending_question_count=sum(
            q.status == MemoirQuestionStatus.PENDING.value for q in questions
        ),
        latest_draft=(
            MemoirDraftResponse.model_validate(latest_draft)
            if latest_draft
            else None
        ),
        questions=[MemoirQuestionResponse.model_validate(q) for q in questions],
    )


async def create_session(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: MemoirSessionCreate | None = None,
) -> MemoirInterviewState:
    await load_memoir_project(db, project_id, user_id)
    title = (req.title.strip() if req else DEFAULT_SESSION_TITLE) or DEFAULT_SESSION_TITLE
    session = MemoirSession(project_id=project_id, title=title)
    db.add(session)
    await db.flush()

    questions = [
        MemoirQuestion(session_id=session.id, prompt=prompt, sort_order=index)
        for index, prompt in enumerate(DEFAULT_MEMOIR_QUESTIONS)
    ]
    db.add_all(questions)
    await db.flush()
    session.current_question_id = questions[0].id if questions else None
    await db.commit()
    return await get_session_state(db, session.id, user_id)


async def get_or_create_current_session(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> MemoirInterviewState:
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirSession)
        .where(
            MemoirSession.project_id == project_id,
            MemoirSession.status == MemoirSessionStatus.ACTIVE.value,
        )
        .order_by(MemoirSession.updated_at.desc())
        .options(
            selectinload(MemoirSession.questions)
            .selectinload(MemoirQuestion.answer)
            .selectinload(MemoirAnswer.draft),
        )
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if not session:
        return await create_session(db, project_id, user_id)

    _advance_loaded_session(session)
    await db.commit()
    return await get_session_state(db, session.id, user_id)


async def current_session_id(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> str | None:
    await load_memoir_project(db, project_id, user_id)
    return await db.scalar(
        select(MemoirSession.id)
        .where(
            MemoirSession.project_id == project_id,
            MemoirSession.status == MemoirSessionStatus.ACTIVE.value,
        )
        .order_by(MemoirSession.updated_at.desc())
        .limit(1)
    )


async def get_session_state(
    db: AsyncSession,
    session_id: str,
    user_id: str,
) -> MemoirInterviewState:
    session = await load_session(db, session_id, user_id)
    return _session_state(session)


async def advance_session(
    db: AsyncSession,
    session_id: str,
    user_id: str,
) -> MemoirSession:
    session = await load_session(db, session_id, user_id)
    _advance_loaded_session(session)
    await db.flush()
    return session


async def get_next_question(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> MemoirQuestionResponse | None:
    state = await get_or_create_current_session(db, project_id, user_id)
    planned_question = await get_or_create_planned_question(
        db,
        project_id,
        user_id,
        state.session.id,
    )
    if planned_question:
        return planned_question
    return state.current_question


async def skip_question(
    db: AsyncSession,
    question_id: str,
    user_id: str,
) -> MemoirInterviewState:
    question = await load_question(db, question_id, user_id)
    if question.status == MemoirQuestionStatus.ANSWERED.value:
        raise HTTPException(status_code=409, detail="已回答的问题不能跳过")
    question.status = MemoirQuestionStatus.SKIPPED.value
    await advance_session(db, question.session_id, user_id)
    await db.commit()
    return await get_session_state(db, question.session_id, user_id)
