from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirGuidanceMode,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.models.projects import Project
from app.schemas.memoir import (
    MemoirVoiceFollowUpRequest,
    MemoirVoiceFollowUpResponse,
)
from app.services import get_ai_service
from app.services.ai_types import AITextClient


SYSTEM_PROMPT = "你是一位耐心、克制的中文回忆录访谈主持人，负责根据老人刚讲的内容自然追问。"
AI_FOLLOW_UP_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
PROMPT_VERSION = "memoir_voice_followup_v1"
MAX_CONTEXT_MESSAGES = 12
logger = logging.getLogger(__name__)


MODULE_LABELS = {
    "person": "人物",
    "event": "事件",
    "place": "地点",
    "period": "时间段",
    "theme": "主题",
}


def _module_label(module: str | None) -> str:
    if not module:
        return "主题"
    return MODULE_LABELS.get(module, module)


def _ordered_messages(messages: list[MemoirChatMessage]) -> list[MemoirChatMessage]:
    return sorted(messages, key=lambda message: (message.turn_index, message.created_at, message.id))


def _recent_context(messages: list[MemoirChatMessage]) -> str:
    lines: list[str] = []
    for message in _ordered_messages(messages)[-MAX_CONTEXT_MESSAGES:]:
        content = message.content_text.strip()
        if not content:
            continue
        speaker = "叙述者" if message.role == MemoirChatMessageRole.USER.value else "AI"
        lines.append(f"{speaker}：{content}")
    return "\n".join(lines) or "暂无对话。"


def _has_user_message(messages: list[MemoirChatMessage]) -> bool:
    return any(
        message.role == MemoirChatMessageRole.USER.value and message.content_text.strip()
        for message in messages
    )


def _build_user_prompt(voice_session: MemoirVoiceSession, *, regenerate: bool) -> str:
    guidance_mode = MemoirGuidanceMode(voice_session.guidance_mode)
    context = _recent_context(list(voice_session.messages))
    module_label = _module_label(voice_session.current_guidance_module)
    regenerate_instruction = "这次是在替换上一句追问，请换一个角度，不要重复上一句。" if regenerate else ""

    if guidance_mode == MemoirGuidanceMode.GUIDED_INTERVIEW:
        mode_instruction = f"""当前是强引导访谈，方向是“{module_label}”。
- 如果还没有叙述者回答，先用一句具体、容易开口的问题引导他进入这个方向
- 如果已有回答，沿着他刚讲的具体人、事、地点、时间或感受追问
- 追问要帮助形成可回忆、可核对的素材，而不是泛泛聊天"""
    else:
        mode_instruction = """当前是自由讲述，AI 只做弱引导。
- 不要强行换主题，不要打断叙述者的时间线
- 如果他刚讲了一段，轻轻追问一个细节或感受
- 如果上下文还很少，鼓励他继续按自己的顺序讲"""

    return f"""请根据下面的访谈上下文，生成下一句 AI 追问。

访谈要求：
- 只输出一句中文问题，不要标题、解释、编号或引号
- 问题要短，最好 30 个汉字以内
- 语气自然、尊重，像真人访谈主持人
- 不要替用户总结事实，不要编造上下文没有的信息
- 不要连续问多个问题
- 不问敏感隐私，除非用户已经主动提到
{regenerate_instruction}

{mode_instruction}

访谈上下文：
{context}
"""


async def _load_voice_session(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSession:
    result = await db.execute(
        select(MemoirVoiceSession)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceSession.id == voice_session_id, Project.user_id == user_id)
        .options(selectinload(MemoirVoiceSession.messages))
    )
    voice_session = result.scalar_one_or_none()
    if not voice_session:
        raise HTTPException(status_code=404, detail="语音会话不存在")
    return voice_session


async def _next_turn_index(db: AsyncSession, voice_session_id: str) -> int:
    result = await db.execute(
        select(func.max(MemoirChatMessage.turn_index)).where(
            MemoirChatMessage.voice_session_id == voice_session_id
        )
    )
    current = result.scalar_one_or_none()
    return 0 if current is None else int(current) + 1


async def generate_voice_follow_up(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceFollowUpRequest,
    ai_client: AITextClient | None = None,
) -> MemoirVoiceFollowUpResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    client = ai_client or get_ai_service(req.ai_model)
    prompt = _build_user_prompt(voice_session, regenerate=req.regenerate)
    try:
        content = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.35,
            max_tokens=180,
            timeout=60.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception(
            "AI voice follow-up failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_FOLLOW_UP_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception(
            "AI voice follow-up failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_FOLLOW_UP_UNAVAILABLE_MESSAGE) from exc

    follow_up_text = str(content).strip().strip("\"“”")
    if not follow_up_text:
        raise HTTPException(status_code=502, detail="AI 未返回可用追问")

    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=follow_up_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent="follow_up",
        turn_index=await _next_turn_index(db, voice_session.id),
        metadata_json={
            "source": "ai.follow_up",
            "prompt_version": PROMPT_VERSION,
            "provider": client.provider_name,
            "model": client.model_name,
            "regenerate": req.regenerate,
            "has_user_message": _has_user_message(list(voice_session.messages)),
        },
    )
    db.add(message)
    await db.commit()
    return MemoirVoiceFollowUpResponse(message=message, follow_up_text=follow_up_text)
