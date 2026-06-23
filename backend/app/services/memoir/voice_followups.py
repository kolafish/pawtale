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
from app.schemas.memoir_voice import (
    MemoirVoiceNoAnswerPromptRequest,
    MemoirVoiceFollowUpRequest,
    MemoirVoiceFollowUpResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient


SYSTEM_PROMPT = (
    "你是一位中文回忆录语音访谈主持人，名字叫“老余”。"
    "你的任务不是审问用户，而是帮助叙述者自然地讲出人生经历、人物、事情、地点、时间和感受。"
)
AI_FOLLOW_UP_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
PROMPT_VERSION = "memoir_voice_followup_v3"
MAX_CONTEXT_MESSAGES = 12
MAX_PREVIOUS_CONTEXT_MESSAGES = 8
FOLLOW_UP_MAX_TOKENS = 180
GUIDED_FOLLOW_UP_MAX_TOKENS = 240
FREE_OPENING_MAX_TOKENS = 260
GUIDED_OPENING_MAX_TOKENS = 320
logger = logging.getLogger(__name__)

NO_ANSWER_PROMPT_VERSION = "memoir_voice_no_answer_prompt_v1"
NO_ANSWER_RETRY_TEXT = "我这边没有听到声音，您可以接着刚才的问题慢慢说。"
NO_ANSWER_PAUSE_TEXT = "没关系，我先暂停一下。您准备好了，再点继续说话。"


MODULE_LABELS = {
    "person": "人物",
    "event": "事件",
    "family": "家庭",
    "work": "工作",
    "place": "地点",
    "period": "时间段",
    "theme": "主题",
    "chat": "闲聊",
}

GUIDED_TOPIC_MENU = "人物、事情、家庭、工作、地点、某一天、人生阶段、闲聊"

GUIDED_TOPIC_LIBRARY = """主题题库参考：
- 人物：聊一个印象最深的人、对你影响最大的人、最敬佩的人、最思念的人、对你帮助最大的人，或者如果可以随便选，你想成为谁。
- 事情：聊一件记忆最深、最生气、最开心、最伤心、现在想起来还想笑、影响最深、最丢脸或最自豪的事情。
- 家庭：聊一个最挂念的家人、一次全家最难忘的团聚、家里发生过的大事、家里的规矩或习惯、一次和家人吵架又和好的经历、家里最辛苦最能撑事的人、从家里学到的东西、过年节日或团聚的记忆。
- 工作：聊第一份工作、一个影响很大的同事领导或师傅、一次最辛苦的工作经历、工作里最自豪的事情、工作中受委屈的事情、一次差点出错或真的出错的经历、靠工作学会的本事、最有成就感的一段工作时期。
- 某一天：聊最热的一天、最冷的一天、最开心的一天、最难过的一天或最累的一天。
- 闲聊：聊一个做过的最美的梦、最恐怖的梦、一个小秘密、印象中最好吃的一顿饭、最早的记忆里有什么，或者最饿的一次是什么时候。"""


def _module_label(module: str | None) -> str:
    if not module:
        return "主题"
    return MODULE_LABELS.get(module, module)


def _ordered_messages(messages: list[MemoirChatMessage]) -> list[MemoirChatMessage]:
    return sorted(messages, key=lambda message: (message.turn_index, message.created_at, message.id))


def _message_context(messages: list[MemoirChatMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        content = message.content_text.strip()
        if not content:
            continue
        speaker = "叙述者" if message.role == MemoirChatMessageRole.USER.value else "AI"
        lines.append(f"{speaker}：{content}")
    return "\n".join(lines)


def _recent_context(messages: list[MemoirChatMessage]) -> str:
    return _message_context(_ordered_messages(messages)[-MAX_CONTEXT_MESSAGES:]) or "暂无对话。"


def _previous_context(messages: list[MemoirChatMessage]) -> str:
    return _message_context(messages) or "暂无历史会话。"


def _has_user_message(messages: list[MemoirChatMessage]) -> bool:
    return any(
        message.role == MemoirChatMessageRole.USER.value and message.content_text.strip()
        for message in messages
    )


def _llm_max_tokens(
    voice_session: MemoirVoiceSession,
    *,
    has_user_message: bool,
    regenerate: bool,
) -> int:
    guidance_mode = MemoirGuidanceMode(voice_session.guidance_mode)
    if not has_user_message:
        if guidance_mode == MemoirGuidanceMode.GUIDED_INTERVIEW:
            return GUIDED_OPENING_MAX_TOKENS
        return FREE_OPENING_MAX_TOKENS
    if guidance_mode == MemoirGuidanceMode.GUIDED_INTERVIEW or regenerate:
        return GUIDED_FOLLOW_UP_MAX_TOKENS
    return FOLLOW_UP_MAX_TOKENS


def _extract_finish_reason(raw_response: dict | None) -> str | None:
    if not raw_response:
        return None
    choices = raw_response.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = first_choice.get("finish_reason")
        if isinstance(finish_reason, str):
            return finish_reason
    status = raw_response.get("status")
    if isinstance(status, str):
        if status == "incomplete":
            incomplete = raw_response.get("incomplete_details")
            if isinstance(incomplete, dict):
                reason = incomplete.get("reason")
                if isinstance(reason, str) and reason:
                    return reason
            return status
        return status
    return None


def _is_truncated_response(finish_reason: str | None) -> bool:
    return finish_reason in {"length", "max_output_tokens", "incomplete"}


def _question_mark_count(text: str) -> int:
    return text.count("？") + text.count("?")


def _usage_metadata(raw_response: dict | None) -> dict:
    if not raw_response:
        return {}
    usage = raw_response.get("usage")
    return usage if isinstance(usage, dict) else {}


def _build_user_prompt(
    voice_session: MemoirVoiceSession,
    *,
    previous_messages: list[MemoirChatMessage],
    regenerate: bool,
) -> str:
    guidance_mode = MemoirGuidanceMode(voice_session.guidance_mode)
    messages = list(voice_session.messages)
    context = _recent_context(messages)
    has_user_message = _has_user_message(messages)
    has_previous_user_message = _has_user_message(previous_messages)
    history_context = _previous_context(previous_messages)
    module_label = _module_label(voice_session.current_guidance_module)
    regenerate_instruction = "这次是在替换上一句追问，请换一个角度，不要重复上一句。" if regenerate else ""

    if guidance_mode == MemoirGuidanceMode.GUIDED_INTERVIEW:
        if has_user_message:
            mode_instruction = f"""当前是主题讲述，当前主题线索是“{module_label}”；如果最近对话中叙述者已经选择了其他主题，以最近对话为准。
- 如果叙述者表达想接着上次聊，结合历史会话进度，围绕上次最后讲到的内容自然接下去
- 如果叙述者表达想重新聊，回到主题菜单，让他重新选一个想聊的方向
- 先从宏观问题开始，不要立刻追细节
- 帮叙述者把当前主题讲完整：时间、地点、人物、发生了什么、当时感受、后来影响
- 不要连续追同一个细节超过两轮
- 如果叙述者明显还有兴趣，就顺着讲；如果回答变短或重复，就引导换一个同主题问题，或回到主题菜单
{GUIDED_TOPIC_LIBRARY}"""
        elif has_previous_user_message:
            mode_instruction = f"""当前是主题讲述继续开场，叙述者之前已有同模式讲述记录。
- 开头要明确体现回访，例如“欢迎回来，咱们接着聊”
- 可以用“老余”轻轻带一下，但不要重复完整自我介绍
- 根据历史会话进度提醒上次讲到哪里，只能复述明确出现过的内容
- 优先邀请叙述者接着上次的话题继续讲，同时允许他重新选主题
- 主题介绍只做一句简短兜底：如果今天想换方向，也可以从这些主题里挑一个：{GUIDED_TOPIC_MENU}
- 不要直接进入细节，不要替叙述者决定继续或重开
- 不要完整展开主题菜单，不要使用首次开场的话术
- 下面题库仅供后续追问参考，开场不要一次展开
{GUIDED_TOPIC_LIBRARY}"""
        else:
            mode_instruction = f"""当前是主题讲述首次开场。
- 这是这个项目第一次主题讲述开场，不要提“上次”或“欢迎回来”
- 先问好，并用“老余”做简短自我介绍
- 先自然说明“今天可以围绕不同主题讲回忆”
- 要明确介绍可聊主题：{GUIDED_TOPIC_MENU}
- 先让叙述者选择想聊哪个主题，不要立刻进入细节
- 可以用一句自然的话完成问候、自我介绍、主题举例和选择邀请
- 下面题库仅供后续追问参考，开场不要一次展开
{GUIDED_TOPIC_LIBRARY}"""
    else:
        if has_user_message:
            mode_instruction = """当前是自由讲述，AI 只做弱引导。
- 如果叙述者表达想接着上次讲，结合历史会话进度，围绕上次最后讲到的时间点或事件轻轻接住
- 顺着叙述者自己的时间线走，尽量不要突然换主题
- 不要一直纠缠同一件事的不同细节
- 如果这件事重要、有情绪或有转折，鼓励他多讲一点
- 如果这件事已经讲得差不多，就自然引导到前后时间、相关人物，或下一段回忆"""
        elif has_previous_user_message:
            mode_instruction = """当前是自由讲述开场，AI 只做弱引导，叙述者之前已有讲述记录。
- 先问好，并用“老余”做简短自我介绍
- 根据历史会话进度提醒上次讲到哪里，只能复述明确出现过的内容
- 温和提示叙述者可以从上次停下来的地方接着讲
- 不要展开主题菜单，不要强行切换主题，不要连续提问"""
        else:
            mode_instruction = """当前是自由讲述开场，AI 只做弱引导。
- 先问好，并用“老余”做简短自我介绍
- 引导叙述者可以从小时候或最早的记忆开始讲起
- 仍然允许叙述者按自己的顺序开始，不要连续提问"""

    return f"""请根据下面的访谈上下文，生成下一句 AI 开场或追问。

访谈要求：
- 只输出一句中文问题或邀请句，不要标题、解释、编号或引号
- 内容要短，普通追问最好 30 个汉字以内；开场或主题菜单可以稍长，但仍要适合语音播放
- 语气自然、尊重，像真人访谈主持人
- 不要编造上下文没有的信息；如果提醒“上次讲到哪里”，只能概括历史会话里明确出现过的内容
- 历史会话进度只用于判断是否续聊；如果历史会话为空，不要提“上次”
- 不要连续问多个问题
- 不问敏感隐私，除非用户已经主动提到
{regenerate_instruction}

{mode_instruction}

访谈上下文：
{context}

历史会话进度：
{history_context}
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


async def _load_previous_project_messages(
    db: AsyncSession,
    voice_session: MemoirVoiceSession,
) -> list[MemoirChatMessage]:
    result = await db.execute(
        select(MemoirChatMessage)
        .join(MemoirVoiceSession, MemoirChatMessage.voice_session_id == MemoirVoiceSession.id)
        .where(
            MemoirChatMessage.project_id == voice_session.project_id,
            MemoirChatMessage.voice_session_id != voice_session.id,
            MemoirVoiceSession.guidance_mode == voice_session.guidance_mode,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            MemoirChatMessage.content_text != "",
        )
        .order_by(
            MemoirChatMessage.created_at.desc(),
            MemoirChatMessage.turn_index.desc(),
            MemoirChatMessage.id.desc(),
        )
        .limit(MAX_PREVIOUS_CONTEXT_MESSAGES)
    )
    return list(reversed(result.scalars().all()))


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
    previous_messages = await _load_previous_project_messages(db, voice_session)
    prompt = _build_user_prompt(
        voice_session,
        previous_messages=previous_messages,
        regenerate=req.regenerate,
    )
    has_user_message = _has_user_message(list(voice_session.messages))
    guidance_intent = "follow_up" if has_user_message else "opening"
    llm_max_tokens = _llm_max_tokens(
        voice_session,
        has_user_message=has_user_message,
        regenerate=req.regenerate,
    )
    raw_response: dict | None = None
    try:
        llm_response = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.35,
            max_tokens=llm_max_tokens,
            timeout=60.0,
            return_raw=True,
        )
        if isinstance(llm_response, tuple):
            content = llm_response[0]
            raw_response = llm_response[1] if isinstance(llm_response[1], dict) else None
        else:
            content = llm_response
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
        raise HTTPException(status_code=502, detail="AI 未返回可用开场或追问")
    finish_reason = _extract_finish_reason(raw_response)

    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=follow_up_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent=guidance_intent,
        turn_index=await _next_turn_index(db, voice_session.id),
        metadata_json={
            "source": f"ai.{guidance_intent}",
            "prompt_version": PROMPT_VERSION,
            "provider": client.provider_name,
            "model": client.model_name,
            "regenerate": req.regenerate,
            "has_user_message": has_user_message,
            "max_tokens": llm_max_tokens,
            "response_chars": len(follow_up_text),
            "question_mark_count": _question_mark_count(follow_up_text),
            "finish_reason": finish_reason,
            "truncated": _is_truncated_response(finish_reason),
            "usage": _usage_metadata(raw_response),
        },
    )
    db.add(message)
    await db.commit()
    return MemoirVoiceFollowUpResponse(message=message, follow_up_text=follow_up_text)


async def create_no_answer_prompt(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceNoAnswerPromptRequest,
) -> MemoirVoiceFollowUpResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    prompt_text = NO_ANSWER_RETRY_TEXT if req.attempt <= 1 else NO_ANSWER_PAUSE_TEXT
    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=prompt_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent="no_answer_prompt",
        turn_index=await _next_turn_index(db, voice_session.id),
        metadata_json={
            "source": "ai.no_answer_prompt",
            "prompt_version": NO_ANSWER_PROMPT_VERSION,
            "attempt": req.attempt,
        },
    )
    db.add(message)
    await db.commit()
    return MemoirVoiceFollowUpResponse(message=message, follow_up_text=prompt_text)
