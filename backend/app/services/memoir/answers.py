from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir import MemoirAnswerStatus, MemoirQuestionStatus
from app.models.memoir import MemoirAnswer
from app.schemas.memoir_legacy import (
    MemoirAnswerResponse,
)
from app.services.memoir.access import load_answer, load_audio_file, load_question
from app.services.memoir.interview import advance_session


def _clean_answer_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise HTTPException(status_code=400, detail="回答不能为空")
    return value


async def _answer_audio_fields(
    db: AsyncSession,
    user_id: str,
    audio_file_id: str | None,
    duration_seconds: int | None,
) -> dict[str, str | int | None]:
    if duration_seconds is not None and duration_seconds < 0:
        raise HTTPException(status_code=400, detail="音频时长不能为负数")
    if not audio_file_id:
        return {}

    audio_file = await load_audio_file(db, audio_file_id, user_id)
    return {
        "audio_file_id": audio_file.id,
        "duration_seconds": (
            duration_seconds if duration_seconds is not None else audio_file.duration_seconds
        ),
    }


async def answer_question_with_text(
    db: AsyncSession,
    question_id: str,
    user_id: str,
    text: str,
    audio_file_id: str | None = None,
    duration_seconds: int | None = None,
) -> MemoirAnswerResponse:
    question = await load_question(db, question_id, user_id)
    if question.status == MemoirQuestionStatus.SKIPPED.value:
        raise HTTPException(status_code=409, detail="已跳过的问题不能回答")

    transcript = _clean_answer_text(text)
    audio_fields = await _answer_audio_fields(db, user_id, audio_file_id, duration_seconds)
    answer = await db.scalar(
        select(MemoirAnswer).where(MemoirAnswer.question_id == question.id)
    )
    if answer:
        answer.transcript_edited = transcript
        if not answer.transcript_raw:
            answer.transcript_raw = transcript
        if answer.status in {
            MemoirAnswerStatus.UPLOADED.value,
            MemoirAnswerStatus.TRANSCRIBING.value,
        }:
            answer.status = MemoirAnswerStatus.ANSWERED.value
        for field, value in audio_fields.items():
            setattr(answer, field, value)
    else:
        answer = MemoirAnswer(
            question_id=question.id,
            transcript_raw=transcript,
            transcript_edited=transcript,
            status=MemoirAnswerStatus.ANSWERED.value,
            **audio_fields,
        )
        db.add(answer)

    question.status = MemoirQuestionStatus.ANSWERED.value
    await db.flush()
    await advance_session(db, question.session_id, user_id)
    await db.commit()
    return MemoirAnswerResponse.model_validate(answer)


async def update_answer_text(
    db: AsyncSession,
    answer_id: str,
    user_id: str,
    text: str,
) -> MemoirAnswerResponse:
    answer = await load_answer(db, answer_id, user_id)
    answer.transcript_edited = _clean_answer_text(text)
    if answer.status in {
        MemoirAnswerStatus.UPLOADED.value,
        MemoirAnswerStatus.TRANSCRIBING.value,
    }:
        answer.status = MemoirAnswerStatus.ANSWERED.value
    answer.question.status = MemoirQuestionStatus.ANSWERED.value
    await db.commit()
    return MemoirAnswerResponse.model_validate(answer)
