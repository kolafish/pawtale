from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir import MemoirDraftStatus, MemoirQuestionStatus
from app.models.memoir import MemoirAnswer, MemoirQuestion, MemoirSession
from app.models.projects import Project
from app.schemas.memoir import (
    MemoirAnswerResponse,
    MemoirDraftResponse,
    MemoirQuestionResponse,
    MemoirStoryCardResponse,
    MemoirStoryDetailResponse,
    MemoirStoryListResponse,
    MemoirSummaryResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.interview import get_or_create_current_session


_PREVIEW_LIMIT = 80


def _preview(text: str | None) -> str:
    value = " ".join((text or "").split())
    if len(value) <= _PREVIEW_LIMIT:
        return value
    return f"{value[:_PREVIEW_LIMIT]}..."


def _week_start(now: datetime) -> datetime:
    return (now - timedelta(days=now.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _story_card(answer: MemoirAnswer) -> MemoirStoryCardResponse:
    question = answer.question
    session = question.session
    draft = answer.draft
    return MemoirStoryCardResponse(
        id=answer.id,
        answer_id=answer.id,
        question_id=question.id,
        session_id=session.id,
        session_title=session.title,
        question_prompt=question.prompt,
        answer_preview=_preview(answer.transcript_edited or answer.transcript_raw),
        answer_status=answer.status,
        draft_id=draft.id if draft else None,
        draft_status=draft.status if draft else None,
        draft_preview=_preview(draft.content_edited or draft.content_raw) if draft else None,
        duration_seconds=answer.duration_seconds,
        created_at=answer.created_at,
        updated_at=answer.updated_at,
    )


def _story_detail(answer: MemoirAnswer) -> MemoirStoryDetailResponse:
    return MemoirStoryDetailResponse(
        story=_story_card(answer),
        question=MemoirQuestionResponse.model_validate(answer.question),
        answer=MemoirAnswerResponse.model_validate(answer),
        draft=MemoirDraftResponse.model_validate(answer.draft) if answer.draft else None,
    )


async def list_story_cards(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> MemoirStoryListResponse:
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirAnswer)
        .join(MemoirQuestion, MemoirAnswer.question_id == MemoirQuestion.id)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .where(MemoirSession.project_id == project_id)
        .options(
            selectinload(MemoirAnswer.question).selectinload(MemoirQuestion.session),
            selectinload(MemoirAnswer.draft),
        )
        .order_by(MemoirAnswer.updated_at.desc(), MemoirAnswer.created_at.desc())
    )
    stories = [_story_card(answer) for answer in result.scalars().all()]
    return MemoirStoryListResponse(stories=stories, total=len(stories))


async def get_story_detail(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    answer_id: str,
) -> MemoirStoryDetailResponse:
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirAnswer)
        .join(MemoirQuestion, MemoirAnswer.question_id == MemoirQuestion.id)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .where(MemoirAnswer.id == answer_id, MemoirSession.project_id == project_id)
        .options(
            selectinload(MemoirAnswer.question).selectinload(MemoirQuestion.session),
            selectinload(MemoirAnswer.draft),
        )
    )
    answer = result.scalar_one_or_none()
    if not answer:
        raise HTTPException(status_code=404, detail="故事不存在")
    return _story_detail(answer)


async def get_summary(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> MemoirSummaryResponse:
    await get_or_create_current_session(db, project_id, user_id)
    stories = (await list_story_cards(db, project_id, user_id)).stories
    questions = (
        await db.execute(
            select(MemoirQuestion.status)
            .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
            .join(Project, MemoirSession.project_id == Project.id)
            .where(MemoirSession.project_id == project_id, Project.user_id == user_id)
        )
    ).scalars().all()
    completed_statuses = {
        MemoirQuestionStatus.ANSWERED.value,
        MemoirQuestionStatus.SKIPPED.value,
    }
    completed_count = sum(status in completed_statuses for status in questions)
    total_questions = len(questions)
    week_start = _week_start(datetime.now(UTC).replace(tzinfo=None))
    pending_review_count = sum(
        story.draft_status == MemoirDraftStatus.DRAFT for story in stories
    )
    confirmed_story_count = sum(
        story.draft_status == MemoirDraftStatus.ACCEPTED for story in stories
    )

    return MemoirSummaryResponse(
        total_story_count=len(stories),
        weekly_story_count=sum(story.created_at >= week_start for story in stories),
        pending_review_count=pending_review_count,
        confirmed_story_count=confirmed_story_count,
        total_question_count=total_questions,
        completed_question_count=completed_count,
        progress_percent=(
            round(completed_count * 100 / total_questions)
            if total_questions
            else 0
        ),
    )
