from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir import MemoirAnswerStatus, MemoirQuestionStatus
from app.models.memoir import MemoirAnswer
from app.schemas.memoir import MemoirAnswerResponse
from app.services.memoir.access import load_answer, load_question
from app.services.memoir.interview import advance_session


def _clean_answer_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise HTTPException(status_code=400, detail="回答不能为空")
    return value


async def answer_question_with_text(
    db: AsyncSession,
    question_id: str,
    user_id: str,
    text: str,
) -> MemoirAnswerResponse:
    question = await load_question(db, question_id, user_id)
    if question.status == MemoirQuestionStatus.SKIPPED.value:
        raise HTTPException(status_code=409, detail="已跳过的问题不能回答")

    transcript = _clean_answer_text(text)
    answer = question.answer
    if answer:
        answer.transcript_edited = transcript
        if not answer.transcript_raw:
            answer.transcript_raw = transcript
        if answer.status in {
            MemoirAnswerStatus.UPLOADED.value,
            MemoirAnswerStatus.TRANSCRIBING.value,
        }:
            answer.status = MemoirAnswerStatus.ANSWERED.value
    else:
        answer = MemoirAnswer(
            question_id=question.id,
            transcript_raw=transcript,
            transcript_edited=transcript,
            status=MemoirAnswerStatus.ANSWERED.value,
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
