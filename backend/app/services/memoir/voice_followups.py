from __future__ import annotations

from dataclasses import dataclass
import logging
import re

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
    MemoirVoiceTurnPlanContext,
    MemoirVoiceTurnPlanRequest,
    MemoirVoiceTurnPlanResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


SYSTEM_PROMPT = (
    "你是一位中文回忆录语音访谈主持人，名字叫“老余”。"
    "你的任务不是审问用户，而是帮助叙述者自然地讲出人生经历、人物、事情、地点、时间和感受。"
)
AI_FOLLOW_UP_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
PROMPT_VERSION = "memoir_voice_interviewer_turn_v1"
TURN_PLAN_PROMPT_VERSION = "memoir_voice_interviewer_turn_plan_v1"
MAX_CONTEXT_MESSAGES = 12
MAX_PREVIOUS_CONTEXT_MESSAGES = 8
TURN_PLAN_MAX_TOKENS = 260
FOLLOW_UP_MAX_TOKENS = 260
GUIDED_FOLLOW_UP_MAX_TOKENS = 320
FREE_OPENING_MAX_TOKENS = 360
GUIDED_OPENING_MAX_TOKENS = 420
logger = logging.getLogger(__name__)

NO_ANSWER_PROMPT_VERSION = "memoir_voice_no_answer_prompt_v1"
NO_ANSWER_RETRY_TEXT = "我这边没有听到声音，您可以接着刚才的问题慢慢说。"
NO_ANSWER_PAUSE_TEXT = "没关系，我先暂停一下。您准备好了，再点继续说话。"


PRIMARY_INTENTS = {
    "opening",
    "follow_up",
    "acknowledge",
    "encourage_continue",
    "reflect_summary",
    "transition",
    "repair",
}
PLANNED_TURN_NON_QUESTION_INTENTS = {
    "acknowledge",
    "encourage_continue",
    "reflect_summary",
}
PLANNED_TURN_QUESTION_INTENTS = {
    "follow_up",
    "transition",
    "repair",
}
TURN_PLAN_USER_SIGNALS = {
    "memory_detail",
    "emotion",
    "short_answer",
    "unclear",
    "off_topic",
    "continuation",
}
TURN_PLAN_RESPONSE_STRATEGIES = {
    "ack_then_follow",
    "direct_follow",
    "transition",
    "repair",
}


@dataclass(frozen=True)
class InterviewerTurn:
    primary_intent: str
    intent_tags: list[str]
    speech_text: str
    expects_answer: bool
    parsed_json: bool


@dataclass(frozen=True)
class VoiceTurnPlan:
    user_signal: str
    response_strategy: str
    should_play_ack: bool
    acknowledgement_text: str
    next_question_brief: str
    parsed_json: bool

    def to_context(self) -> MemoirVoiceTurnPlanContext:
        return MemoirVoiceTurnPlanContext(
            user_signal=self.user_signal,
            response_strategy=self.response_strategy,
            should_play_ack=self.should_play_ack,
            acknowledgement_text=self.acknowledgement_text,
            next_question_brief=self.next_question_brief,
        )

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

GUIDED_TOPIC_KEYWORDS = {
    "person": ("人物", "人", "朋友", "同学", "亲人", "老师"),
    "event": ("事情", "事件", "经历", "事儿"),
    "family": ("家庭", "家人", "家里", "父母", "外婆", "爷爷", "奶奶"),
    "work": ("工作", "上班", "单位", "职场", "同事", "领导", "师傅", "升职"),
    "place": ("地点", "地方", "老家", "院子", "学校"),
    "period": ("人生阶段", "时间段", "小时候", "童年", "年轻时候"),
    "chat": ("闲聊", "随便聊", "梦", "吃的"),
}

GUIDED_TOPIC_SWITCH_PATTERNS = (
    r"(聊|聊聊|讲讲|说说|谈谈|换到|换成|换个|换一个|主题|方向).{0,8}{keyword}",
    r"{keyword}.{0,4}(吧|主题|方向)",
)


def _module_label(module: str | None) -> str:
    if not module:
        return "主题"
    return MODULE_LABELS.get(module, module)


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())


def _latest_user_text(messages: list[MemoirChatMessage]) -> str:
    for message in reversed(_ordered_messages(messages)):
        if message.role == MemoirChatMessageRole.USER.value and message.content_text.strip():
            return message.content_text.strip()
    return ""


def _requested_guidance_topic(text: str) -> tuple[str, str] | None:
    normalized = _compact_text(text)
    if not normalized:
        return None
    for module, keywords in GUIDED_TOPIC_KEYWORDS.items():
        for keyword in keywords:
            escaped_keyword = re.escape(keyword)
            if any(
                re.search(pattern.replace("{keyword}", escaped_keyword), normalized)
                for pattern in GUIDED_TOPIC_SWITCH_PATTERNS
            ):
                return module, _module_label(module)
    return None


def _requested_topic_hint(messages: list[MemoirChatMessage]) -> str:
    requested = _requested_guidance_topic(_latest_user_text(messages))
    if not requested:
        return ""
    _, label = requested
    return f"叙述者最新一句是在明确选择或切换到“{label}”主题。下一轮必须围绕“{label}”展开，不要继续更早的旧话题。"


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


def _acknowledgement_message(
    voice_session: MemoirVoiceSession,
    message_id: str | None,
) -> MemoirChatMessage | None:
    if not message_id:
        return None
    for message in voice_session.messages:
        if message.id != message_id:
            continue
        metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
        if (
            message.role == MemoirChatMessageRole.ASSISTANT.value
            and message.guidance_intent in {"acknowledge", "encourage_continue", "reflect_summary"}
            and metadata.get("expects_answer") is False
        ):
            return message
        raise HTTPException(status_code=400, detail="承接消息不能用于生成追问")
    raise HTTPException(status_code=400, detail="承接消息不存在")


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


def _clean_speech_text(value: object) -> str:
    return str(value or "").strip().strip("\"“”").strip()


def _normalize_primary_intent(value: object, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized in PRIMARY_INTENTS else fallback


def _normalize_intent_tags(value: object, primary_intent: str) -> list[str]:
    raw_tags = value if isinstance(value, list) else []
    tags: list[str] = []
    for item in raw_tags:
        tag = str(item or "").strip()
        if tag in PRIMARY_INTENTS and tag not in tags:
            tags.append(tag)
    if primary_intent not in tags:
        tags.append(primary_intent)
    return tags[:5]


def _parse_interviewer_turn(
    content: object,
    *,
    fallback_intent: str,
    default_expects_answer: bool = True,
) -> InterviewerTurn:
    raw_text = _clean_speech_text(content)
    payload = extract_json_payload(raw_text)
    if isinstance(payload, dict):
        speech_text = _clean_speech_text(
            payload.get("speech_text")
            or payload.get("text")
            or payload.get("follow_up_text")
            or payload.get("message")
        )
        if speech_text:
            primary_intent = _normalize_primary_intent(
                payload.get("primary_intent") or payload.get("intent"),
                fallback_intent,
            )
            expects_answer = payload.get("expects_answer")
            return InterviewerTurn(
                primary_intent=primary_intent,
                intent_tags=_normalize_intent_tags(payload.get("intent_tags"), primary_intent),
                speech_text=speech_text,
                expects_answer=expects_answer if isinstance(expects_answer, bool) else default_expects_answer,
                parsed_json=True,
            )
    return InterviewerTurn(
        primary_intent=fallback_intent,
        intent_tags=[fallback_intent],
        speech_text=raw_text,
        expects_answer=default_expects_answer,
        parsed_json=False,
    )


def _bounded_speech_text(value: object, *, max_chars: int) -> str:
    text = _clean_speech_text(value)
    text = " ".join(text.split())
    return text[:max_chars]


def _normalize_turn_plan_value(value: object, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized in allowed else fallback


def _parse_voice_turn_plan(content: object) -> VoiceTurnPlan:
    raw_text = _clean_speech_text(content)
    payload = extract_json_payload(raw_text)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="AI 未返回可用访谈计划")

    response_strategy = _normalize_turn_plan_value(
        payload.get("response_strategy"),
        TURN_PLAN_RESPONSE_STRATEGIES,
        "direct_follow",
    )
    should_play_ack = payload.get("should_play_ack")
    if not isinstance(should_play_ack, bool):
        should_play_ack = response_strategy == "ack_then_follow"

    acknowledgement_text = _bounded_speech_text(payload.get("acknowledgement_text"), max_chars=80)
    if should_play_ack and not acknowledgement_text:
        should_play_ack = False
        response_strategy = "direct_follow"
    if not should_play_ack:
        acknowledgement_text = ""
        if response_strategy == "ack_then_follow":
            response_strategy = "direct_follow"
    elif response_strategy == "direct_follow":
        response_strategy = "ack_then_follow"

    next_question_brief = _bounded_speech_text(payload.get("next_question_brief"), max_chars=160)
    if not next_question_brief:
        raise HTTPException(status_code=502, detail="AI 未返回下一问计划")

    return VoiceTurnPlan(
        user_signal=_normalize_turn_plan_value(
            payload.get("user_signal"),
            TURN_PLAN_USER_SIGNALS,
            "memory_detail",
        ),
        response_strategy=response_strategy,
        should_play_ack=should_play_ack,
        acknowledgement_text=acknowledgement_text,
        next_question_brief=next_question_brief,
        parsed_json=True,
    )


def _voice_turn_plan_from_request(plan: MemoirVoiceTurnPlanContext | None) -> VoiceTurnPlan | None:
    if not plan:
        return None
    response_strategy = _normalize_turn_plan_value(
        plan.response_strategy,
        TURN_PLAN_RESPONSE_STRATEGIES,
        "direct_follow",
    )
    should_play_ack = bool(plan.should_play_ack)
    acknowledgement_text = _bounded_speech_text(plan.acknowledgement_text, max_chars=80)
    if should_play_ack and not acknowledgement_text:
        should_play_ack = False
        response_strategy = "direct_follow"
    if not should_play_ack:
        acknowledgement_text = ""
        if response_strategy == "ack_then_follow":
            response_strategy = "direct_follow"
    elif response_strategy == "direct_follow":
        response_strategy = "ack_then_follow"
    next_question_brief = _bounded_speech_text(plan.next_question_brief, max_chars=160)
    if not next_question_brief:
        raise HTTPException(status_code=400, detail="下一问计划不能为空")
    return VoiceTurnPlan(
        user_signal=_normalize_turn_plan_value(
            plan.user_signal,
            TURN_PLAN_USER_SIGNALS,
            "memory_detail",
        ),
        response_strategy=response_strategy,
        should_play_ack=should_play_ack,
        acknowledgement_text=acknowledgement_text,
        next_question_brief=next_question_brief,
        parsed_json=True,
    )


def _align_plan_to_requested_topic(plan: VoiceTurnPlan, messages: list[MemoirChatMessage]) -> VoiceTurnPlan:
    requested = _requested_guidance_topic(_latest_user_text(messages))
    if not requested:
        return plan
    _, label = requested
    return VoiceTurnPlan(
        user_signal="off_topic",
        response_strategy="transition",
        should_play_ack=False,
        acknowledgement_text="",
        next_question_brief=f"用户明确想聊{label}，下一问直接围绕{label}经历展开，不继续旧话题",
        parsed_json=plan.parsed_json,
    )


def _planned_question_turn(turn: InterviewerTurn) -> InterviewerTurn:
    primary_intent = (
        turn.primary_intent
        if turn.primary_intent in PLANNED_TURN_QUESTION_INTENTS
        else "follow_up"
    )
    intent_tags = [
        tag for tag in turn.intent_tags
        if tag not in PLANNED_TURN_NON_QUESTION_INTENTS
    ]
    if primary_intent not in intent_tags:
        intent_tags.append(primary_intent)
    return InterviewerTurn(
        primary_intent=primary_intent,
        intent_tags=intent_tags[:5],
        speech_text=turn.speech_text,
        expects_answer=True,
        parsed_json=turn.parsed_json,
    )


def _build_user_prompt(
    voice_session: MemoirVoiceSession,
    *,
    previous_messages: list[MemoirChatMessage],
    regenerate: bool,
    acknowledgement_message_id: str | None = None,
    turn_plan: VoiceTurnPlan | None = None,
) -> str:
    guidance_mode = MemoirGuidanceMode(voice_session.guidance_mode)
    messages = list(voice_session.messages)
    context = _recent_context(messages)
    has_user_message = _has_user_message(messages)
    has_previous_user_message = _has_user_message(previous_messages)
    history_context = _previous_context(previous_messages)
    module_label = _module_label(voice_session.current_guidance_module)
    topic_hint = _requested_topic_hint(messages)
    regenerate_instruction = "这次是在替换上一句回应，请换一个角度，不要重复上一句。" if regenerate else ""
    if turn_plan:
        played_ack = "已播放短承接" if turn_plan.should_play_ack else "未播放短承接"
        ack_text = turn_plan.acknowledgement_text or "无"
        pipeline_instruction = f"""本轮已经先做过结构化访谈计划：
- 用户信号：{turn_plan.user_signal}
- 回应策略：{turn_plan.response_strategy}
- 短承接状态：{played_ack}
- 短承接内容：{ack_text}
- 下一问方向：{turn_plan.next_question_brief}
这次只生成正式的下一句主持人回应；优先落实“下一问方向”。
不要重复短承接内容，不要补一段新的承接或小结；如果策略是 direct_follow，就直接进入问题。
primary_intent 优先 follow_up、transition 或 repair，intent_tags 不要包含 acknowledge、encourage_continue、reflect_summary。"""
    else:
        pipeline_instruction = (
            "上一条老余回应已经完成了短承接；这次只生成下一句推进访谈的问题或自然转向。"
            "不要再承接、认可、概括或复述叙述者刚才说的内容，"
            "不要用“您刚才说”“听起来”“这个画面”“这段经历”这类开头；"
            "primary_intent 优先 follow_up 或 transition，intent_tags 不要包含 acknowledge、encourage_continue、reflect_summary。"
            if acknowledgement_message_id
            else ""
        )

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

    return f"""请根据下面的访谈上下文，生成下一句“老余”的主持人回应。

访谈要求：
- 你可以自主选择下一步动作：开场、继续追问、承接认可、鼓励继续讲、小结、自然换方向，或没听清/回答太短时温和修复
- 允许把多个动作组合在一句回应里，例如“承接和认可 + 鼓励继续讲 + 一个追问”
- speech_text 要像真人主持人自然说话，通常 1 到 3 句，适合语音播放
- 普通回应建议 40 到 90 个汉字；开场或主题菜单可以稍长，但不要啰嗦
- 不要机械夸奖，不要说空泛的“您真棒”；认可必须落在叙述者刚说过的具体内容上
- 不要编造上下文没有的信息；如果提醒“上次讲到哪里”，只能概括历史会话里明确出现过的内容
- 历史会话进度只用于判断是否续聊；如果历史会话为空，不要提“上次”
- 一次最多推进一个方向；如果要问问题，只问一个自然问题
- 不问敏感隐私，除非用户已经主动提到
- 遇到痛苦、遗憾、疾病、离别等内容，可以温和承接，不要强行积极
{regenerate_instruction}
{pipeline_instruction}
{topic_hint}

{mode_instruction}

访谈上下文：
{context}

历史会话进度：
{history_context}

请只输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "primary_intent": "follow_up",
  "intent_tags": ["acknowledge", "encourage_continue", "follow_up"],
  "speech_text": "老余实际要说的话",
  "expects_answer": true
}}

字段说明：
- primary_intent 表示这句话最终的主动作，只能从 opening、follow_up、acknowledge、encourage_continue、reflect_summary、transition、repair 里选一个
- intent_tags 表示这句话包含的表达功能，可以多个，必须包含 primary_intent
- speech_text 是要播放给用户听的完整中文回应
- expects_answer 第一版通常填 true；只有纯小结且不希望立刻收音时才填 false
"""


def _build_turn_plan_prompt(
    voice_session: MemoirVoiceSession,
    *,
    previous_messages: list[MemoirChatMessage],
) -> str:
    messages = list(voice_session.messages)
    context = _recent_context(messages)
    history_context = _previous_context(previous_messages)
    guidance_mode = MemoirGuidanceMode(voice_session.guidance_mode)
    module_label = _module_label(voice_session.current_guidance_module)
    topic_hint = _requested_topic_hint(messages)
    mode_hint = (
        f"当前是主题讲述，当前主题线索是“{module_label}”。计划要服务于当前主题，但如果用户已经自然换题，以最近对话为准。"
        if guidance_mode == MemoirGuidanceMode.GUIDED_INTERVIEW
        else "当前是自由讲述。计划要顺着叙述者自己的时间线和感受，不要突然换主题。"
    )
    return f"""请先为“老余”的下一轮语音访谈做结构化计划，不要生成正式追问正文。

计划目标：
- 判断叙述者刚才这段更像哪类信号：memory_detail、emotion、short_answer、unclear、off_topic、continuation
- 决定回应策略：ack_then_follow、direct_follow、transition、repair
- 只有当短承接能明显增加被理解感时，should_play_ack 才填 true；否则直接 direct_follow
- 短承接不是必须的。用户回答短、信息少、或已经需要直接追问时，不要硬加第一段
- acknowledgement_text 只在 should_play_ack=true 时填写，必须自然、简短、不是问题，8 到 28 个汉字
- acknowledgement_text 不要复述整句，不要把刚才的人名、地点、事件串起来，不要用“您刚才说”“你提到”“这个画面”“这段经历”开头
- next_question_brief 是给下一步生成正式追问的内部方向，不是要播放的原文，10 到 60 个汉字
- next_question_brief 要说清下一问应该推进什么，但不要和 acknowledgement_text 重复同一句意思
- 不要编造上下文没有的信息

{mode_hint}
{topic_hint}

访谈上下文：
{context}

历史会话进度：
{history_context}

请只输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "user_signal": "memory_detail",
  "response_strategy": "ack_then_follow",
  "should_play_ack": true,
  "acknowledgement_text": "这段日子听着很有生活气。",
  "next_question_brief": "继续问当时谁和叙述者一起做这件事"
}}

字段说明：
- user_signal 只能从 memory_detail、emotion、short_answer、unclear、off_topic、continuation 里选一个
- response_strategy 只能从 ack_then_follow、direct_follow、transition、repair 里选一个
- should_play_ack 表示是否需要播放一个短承接
- acknowledgement_text 是可选短承接；如果 should_play_ack=false，必须为空字符串
- next_question_brief 是下一句正式回应的生成方向
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
        raise HTTPException(status_code=404, detail="本次语音记录不存在")
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


async def _call_interviewer_llm(
    client: AITextClient,
    *,
    prompt: str,
    max_tokens: int,
) -> tuple[object, dict | None]:
    try:
        llm_response = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.35,
            max_tokens=max_tokens,
            timeout=60.0,
            return_raw=True,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception(
            "AI voice interviewer turn failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_FOLLOW_UP_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception(
            "AI voice interviewer turn failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_FOLLOW_UP_UNAVAILABLE_MESSAGE) from exc

    if isinstance(llm_response, tuple):
        return llm_response[0], llm_response[1] if isinstance(llm_response[1], dict) else None
    return llm_response, None


async def _save_interviewer_turn(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    turn: InterviewerTurn,
    client: AITextClient,
    prompt_version: str,
    max_tokens: int,
    raw_response: dict | None,
    fallback_intent: str,
    metadata_extra: dict[str, object],
) -> MemoirVoiceFollowUpResponse:
    follow_up_text = turn.speech_text
    if not follow_up_text:
        raise HTTPException(status_code=502, detail="AI 未返回可用访谈回应")
    finish_reason = _extract_finish_reason(raw_response)
    metadata = {
        "source": f"ai.{turn.primary_intent}",
        "prompt_version": prompt_version,
        "fallback_intent": fallback_intent,
        "primary_intent": turn.primary_intent,
        "intent_tags": turn.intent_tags,
        "expects_answer": turn.expects_answer,
        "parsed_json": turn.parsed_json,
        "provider": client.provider_name,
        "model": client.model_name,
        "max_tokens": max_tokens,
        "response_chars": len(follow_up_text),
        "question_mark_count": _question_mark_count(follow_up_text),
        "finish_reason": finish_reason,
        "truncated": _is_truncated_response(finish_reason),
        "usage": _usage_metadata(raw_response),
    }
    metadata.update(metadata_extra)
    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=follow_up_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent=turn.primary_intent,
        turn_index=await _next_turn_index(db, voice_session.id),
        metadata_json=metadata,
    )
    db.add(message)
    await db.commit()
    return MemoirVoiceFollowUpResponse(
        message=message,
        follow_up_text=follow_up_text,
        primary_intent=turn.primary_intent,
        intent_tags=turn.intent_tags,
        expects_answer=turn.expects_answer,
    )


async def generate_voice_follow_up(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceFollowUpRequest,
    ai_client: AITextClient | None = None,
) -> MemoirVoiceFollowUpResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    acknowledgement_message = _acknowledgement_message(voice_session, req.acknowledgement_message_id)
    client = ai_client or get_ai_service(req.ai_model)
    previous_messages = await _load_previous_project_messages(db, voice_session)
    turn_plan = _voice_turn_plan_from_request(req.turn_plan)
    prompt = _build_user_prompt(
        voice_session,
        previous_messages=previous_messages,
        regenerate=req.regenerate,
        acknowledgement_message_id=acknowledgement_message.id if acknowledgement_message else None,
        turn_plan=turn_plan,
    )
    has_user_message = _has_user_message(list(voice_session.messages))
    fallback_intent = "follow_up" if has_user_message else "opening"
    llm_max_tokens = _llm_max_tokens(
        voice_session,
        has_user_message=has_user_message,
        regenerate=req.regenerate,
    )
    content, raw_response = await _call_interviewer_llm(
        client,
        prompt=prompt,
        max_tokens=llm_max_tokens,
    )
    turn = _parse_interviewer_turn(content, fallback_intent=fallback_intent)
    if acknowledgement_message or turn_plan:
        turn = _planned_question_turn(turn)
    return await _save_interviewer_turn(
        db,
        voice_session=voice_session,
        turn=turn,
        client=client,
        prompt_version=PROMPT_VERSION,
        max_tokens=llm_max_tokens,
        raw_response=raw_response,
        fallback_intent=fallback_intent,
        metadata_extra={
            "regenerate": req.regenerate,
            "has_user_message": has_user_message,
            "turn_pipeline_stage": "question",
            "acknowledgement_message_id": acknowledgement_message.id if acknowledgement_message else None,
            "turn_plan": turn_plan.to_context().model_dump() if turn_plan else None,
        },
    )


async def plan_voice_turn(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceTurnPlanRequest,
    ai_client: AITextClient | None = None,
) -> MemoirVoiceTurnPlanResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    messages = list(voice_session.messages)
    if not _has_user_message(messages):
        raise HTTPException(status_code=400, detail="还没有可规划的讲述内容")
    client = ai_client or get_ai_service(req.ai_model)
    previous_messages = await _load_previous_project_messages(db, voice_session)
    prompt = _build_turn_plan_prompt(voice_session, previous_messages=previous_messages)
    content, raw_response = await _call_interviewer_llm(
        client,
        prompt=prompt,
        max_tokens=TURN_PLAN_MAX_TOKENS,
    )
    plan = _align_plan_to_requested_topic(_parse_voice_turn_plan(content), messages)
    acknowledgement: MemoirVoiceFollowUpResponse | None = None
    if plan.should_play_ack:
        turn = InterviewerTurn(
            primary_intent="acknowledge",
            intent_tags=["acknowledge"],
            speech_text=plan.acknowledgement_text,
            expects_answer=False,
            parsed_json=plan.parsed_json,
        )
        acknowledgement = await _save_interviewer_turn(
            db,
            voice_session=voice_session,
            turn=turn,
            client=client,
            prompt_version=TURN_PLAN_PROMPT_VERSION,
            max_tokens=TURN_PLAN_MAX_TOKENS,
            raw_response=raw_response,
            fallback_intent="acknowledge",
            metadata_extra={
                "has_user_message": True,
                "turn_pipeline_stage": "acknowledgement",
                "turn_plan": plan.to_context().model_dump(),
            },
        )
    return MemoirVoiceTurnPlanResponse(
        plan=plan.to_context(),
        acknowledgement=acknowledgement,
    )


async def create_no_answer_prompt(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceNoAnswerPromptRequest,
) -> MemoirVoiceFollowUpResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    prompt_text = NO_ANSWER_RETRY_TEXT if req.attempt <= 1 else NO_ANSWER_PAUSE_TEXT
    expects_answer = req.attempt <= 1
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
            "primary_intent": "repair",
            "intent_tags": ["repair"],
            "expects_answer": expects_answer,
            "attempt": req.attempt,
        },
    )
    db.add(message)
    await db.commit()
    return MemoirVoiceFollowUpResponse(
        message=message,
        follow_up_text=prompt_text,
        primary_intent="repair",
        intent_tags=["repair"],
        expects_answer=expects_answer,
    )
