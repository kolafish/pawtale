import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir import MemoirAnswerStatus, MemoirDraftStatus
from app.models.memoir import MemoirDraft
from app.schemas.memoir import (
    MemoirDraftEditRequest,
    MemoirDraftGenerateRequest,
    MemoirDraftResponse,
)
from app.services import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir.access import load_answer, load_draft


SYSTEM_PROMPT = "你是一位谨慎的中文口述稿校对助手，只做忠于原话的转写校正和口头语清理。"
AI_DRAFT_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
logger = logging.getLogger(__name__)


def _answer_text(raw: str, edited: str) -> str:
    text = (edited or raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="回答内容为空，无法整理")
    return text


def _build_user_prompt(question_prompt: str, answer_text: str) -> str:
    return f"""请根据访谈问题上下文，对用户回答做一次忠于原文的口述稿校正。

要求：
- 只修正 ASR 可能因方言、口音、同音词造成的明显错字或错词；无法确定时保留原文
- 去掉明显的口头填充词，比如“嗯”“啊”“呃”“那个”“就是”等不承载信息的重复语气词
- 可以整理基础标点和极少量语序，使句子可读
- 不新增事实、人物、时间、地点、事件或情绪
- 不扩写、不总结、不文学化、不替用户补充细节
- 输出长度应接近用户回答；短回答保持短
- 直接输出清理后的第一人称回答正文，不要标题、列表、解释或引号

访谈问题（仅用于判断转写纠错上下文，不用于扩写）：
{question_prompt}

用户回答：
{answer_text}
"""


async def _generate_draft_text(
    answer_text: str,
    question_prompt: str,
    req: MemoirDraftGenerateRequest,
    ai_client: AITextClient | None,
) -> str:
    client = ai_client or get_ai_service(req.ai_model)
    try:
        content = await client._call_llm(
            SYSTEM_PROMPT,
            _build_user_prompt(question_prompt, answer_text),
            temperature=0.2,
            max_tokens=1200,
            timeout=90.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception(
            "AI memoir draft failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_DRAFT_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception(
            "AI memoir draft failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_DRAFT_UNAVAILABLE_MESSAGE) from exc
    draft_text = str(content).strip()
    if not draft_text:
        raise HTTPException(status_code=502, detail="AI未返回可用整理稿")
    return draft_text


async def generate_draft_for_answer(
    db: AsyncSession,
    answer_id: str,
    user_id: str,
    req: MemoirDraftGenerateRequest,
    ai_client: AITextClient | None = None,
) -> MemoirDraftResponse:
    answer = await load_answer(db, answer_id, user_id)
    if answer.status == MemoirAnswerStatus.CONFIRMED.value:
        raise HTTPException(status_code=409, detail="已确认的回答不能重新整理")

    answer_text = _answer_text(answer.transcript_raw, answer.transcript_edited)
    draft_text = await _generate_draft_text(
        answer_text,
        answer.question.prompt,
        req,
        ai_client,
    )

    if answer.draft:
        draft = answer.draft
        draft.content_raw = draft_text
        draft.content_edited = draft_text
        draft.status = MemoirDraftStatus.DRAFT.value
    else:
        draft = MemoirDraft(
            answer_id=answer.id,
            content_raw=draft_text,
            content_edited=draft_text,
            status=MemoirDraftStatus.DRAFT.value,
        )
        db.add(draft)

    answer.status = MemoirAnswerStatus.DRAFTED.value
    await db.commit()
    return MemoirDraftResponse.model_validate(draft)


async def update_draft_content(
    db: AsyncSession,
    draft_id: str,
    user_id: str,
    req: MemoirDraftEditRequest,
) -> MemoirDraftResponse:
    draft = await load_draft(db, draft_id, user_id)
    if draft.status == MemoirDraftStatus.ACCEPTED.value:
        raise HTTPException(status_code=409, detail="已确认的整理稿不能编辑")
    content = req.content_edited.strip()
    if not content:
        raise HTTPException(status_code=400, detail="整理稿不能为空")
    draft.content_edited = content
    await db.commit()
    return MemoirDraftResponse.model_validate(draft)


async def accept_draft(
    db: AsyncSession,
    draft_id: str,
    user_id: str,
) -> MemoirDraftResponse:
    draft = await load_draft(db, draft_id, user_id)
    if not draft.content_edited.strip():
        raise HTTPException(status_code=400, detail="整理稿不能为空")
    draft.status = MemoirDraftStatus.ACCEPTED.value
    draft.answer.status = MemoirAnswerStatus.CONFIRMED.value
    await db.commit()
    return MemoirDraftResponse.model_validate(draft)
