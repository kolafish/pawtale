from __future__ import annotations

from dataclasses import dataclass
import re

from app.domain.memoir.types import MemoirChatMessageRole, MemoirGuidanceMode
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.services.json_utils import extract_json_payload


SYSTEM_PROMPT = (
    "你是一位中文回忆录语音访谈主持人，名字叫“老余”。"
    "你的任务不是审问用户，而是帮助叙述者自然地讲出人生经历、人物、事情、地点、时间和感受。"
)
PROMPT_VERSION = "memoir_voice_interviewer_turn_v1"
MAX_CONTEXT_MESSAGES = 12
FOLLOW_UP_MAX_TOKENS = 260
GUIDED_FOLLOW_UP_MAX_TOKENS = 320
FREE_OPENING_MAX_TOKENS = 360
GUIDED_OPENING_MAX_TOKENS = 420


PRIMARY_INTENTS = {
    "opening",
    "follow_up",
    "acknowledge",
    "encourage_continue",
    "reflect_summary",
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


def _question_mark_count(text: str) -> int:
    return text.count("？") + text.count("?")


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
