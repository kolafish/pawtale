from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirAnswer, MemoirDraft, MemoirQuestion, MemoirSession
from app.models.projects import Project


def _session_load_options():
    return (
        selectinload(MemoirSession.questions)
        .selectinload(MemoirQuestion.answer)
        .selectinload(MemoirAnswer.draft),
    )


async def load_memoir_project(db: AsyncSession, project_id: str, user_id: str) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")
    return project


async def load_session(db: AsyncSession, session_id: str, user_id: str) -> MemoirSession:
    result = await db.execute(
        select(MemoirSession)
        .join(Project, MemoirSession.project_id == Project.id)
        .where(MemoirSession.id == session_id, Project.user_id == user_id)
        .options(*_session_load_options())
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="访谈不存在")
    return session


async def load_question(db: AsyncSession, question_id: str, user_id: str) -> MemoirQuestion:
    result = await db.execute(
        select(MemoirQuestion)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .join(Project, MemoirSession.project_id == Project.id)
        .where(MemoirQuestion.id == question_id, Project.user_id == user_id)
        .options(selectinload(MemoirQuestion.answer).selectinload(MemoirAnswer.draft))
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="问题不存在")
    return question


async def load_answer(db: AsyncSession, answer_id: str, user_id: str) -> MemoirAnswer:
    result = await db.execute(
        select(MemoirAnswer)
        .join(MemoirQuestion, MemoirAnswer.question_id == MemoirQuestion.id)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .join(Project, MemoirSession.project_id == Project.id)
        .where(MemoirAnswer.id == answer_id, Project.user_id == user_id)
        .options(
            selectinload(MemoirAnswer.question),
            selectinload(MemoirAnswer.draft),
        )
    )
    answer = result.scalar_one_or_none()
    if not answer:
        raise HTTPException(status_code=404, detail="回答不存在")
    return answer


async def load_draft(db: AsyncSession, draft_id: str, user_id: str) -> MemoirDraft:
    result = await db.execute(
        select(MemoirDraft)
        .join(MemoirAnswer, MemoirDraft.answer_id == MemoirAnswer.id)
        .join(MemoirQuestion, MemoirAnswer.question_id == MemoirQuestion.id)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .join(Project, MemoirSession.project_id == Project.id)
        .where(MemoirDraft.id == draft_id, Project.user_id == user_id)
        .options(selectinload(MemoirDraft.answer))
    )
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="整理稿不存在")
    return draft
