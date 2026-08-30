from __future__ import annotations

from dataclasses import dataclass
import re

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirChatMessageRole,
    MemoirGuidanceMode,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.schemas.memoir_voice import MemoirAutomationEventResponse, MemoirProjectProgressSnapshotResponse
from app.services.json_utils import extract_json_payload


SYSTEM_PROMPT = (
    "你是一位中文回忆录语音访谈主持人。"
    "你的任务不是审问用户，而是帮助叙述者自然地讲出人生经历、人物、事情、地点、时间和感受。"
)
MAX_CONTEXT_MESSAGES = 12
FOLLOW_UP_MAX_TOKENS = 260
GUIDED_FOLLOW_UP_MAX_TOKENS = 320
FREE_OPENING_MAX_TOKENS = 360
GUIDED_OPENING_MAX_TOKENS = 420
PROGRESS_PROMPT_VERSION = "memoir_voice_progress_context_v2"
PROGRESS_UTTERANCE_MILESTONES = {1, 3, 5, 10, 15, 20, 30, 50, 75, 100}
SPEAKER_LABELS = ("小余", "老余", "AI", "Ai", "ai", "助手", "主持人")


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


@dataclass(frozen=True)
class ProgressPromptContext:
    prompt_text: str
    progress_update: dict[str, object] | None = None
    automation_event_ids: list[str] | None = None
    speech_prefix: str = ""
    voice_announcement_event_ids: list[str] | None = None


@dataclass(frozen=True)
class InterviewerPersona:
    host_name: str
    voice_gender: str


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


def interviewer_persona_for_voice(voice: str | None) -> InterviewerPersona:
    normalized = (voice or "").strip().lower()
    if "female" in normalized or "xiaohe" in normalized:
        return InterviewerPersona(host_name="小余", voice_gender="female")
    if "male" in normalized or "m191" in normalized:
        return InterviewerPersona(host_name="老余", voice_gender="male")
    return InterviewerPersona(host_name="老余", voice_gender="unknown")


def system_prompt_for_persona(persona: InterviewerPersona) -> str:
    return f"{SYSTEM_PROMPT}你在对话中自称“{persona.host_name}”。"


GUIDED_TOPIC_MENU = "人物、事情、家庭、工作、地点、某一天、人生阶段、闲聊"

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


def build_progress_prompt_context(snapshot: MemoirProjectProgressSnapshotResponse) -> ProgressPromptContext:
    selected_update = _automation_event_progress_update(snapshot.recent_automation_events)
    if selected_update is not None:
        selected_update = _with_snapshot_facts(selected_update, snapshot)
    if selected_update is None:
        selected_update = _snapshot_progress_update(snapshot)

    if not selected_update:
        return ProgressPromptContext(
            prompt_text=(
                "项目进度事实：本轮没有可播报的结构化进度变化。不要编造记录数、章节名、成文字数或自动整理结果。"
            ),
            progress_update=None,
            automation_event_ids=[],
            speech_prefix="",
            voice_announcement_event_ids=[],
        )

    event_ids = selected_update.get("event_ids")
    automation_event_ids = [str(item) for item in event_ids] if isinstance(event_ids, list) else []
    speech_prefix = _progress_speech_prefix(selected_update)
    voice_announcement_event_ids = automation_event_ids if speech_prefix else []
    if speech_prefix:
        selected_update = {
            **selected_update,
            "speech_prefix": speech_prefix,
            "voice_announcement_event_ids": voice_announcement_event_ids,
        }
    prompt_text = _progress_prompt_text(selected_update)
    return ProgressPromptContext(
        prompt_text=prompt_text,
        progress_update=selected_update,
        automation_event_ids=automation_event_ids,
        speech_prefix=speech_prefix,
        voice_announcement_event_ids=voice_announcement_event_ids,
    )


def _progress_prompt_text(selected_update: dict[str, object]) -> str:
    lines = [
        "项目进度事实（调试期偏主动；只允许引用以下事实，不要编造数字、章节名或自动整理结果）：",
        f"- 本轮可选进度鼓励：{selected_update['text']}",
    ]
    speech_prefix = selected_update.get("speech_prefix")
    has_speech_prefix = isinstance(speech_prefix, str) and bool(speech_prefix.strip())
    if has_speech_prefix:
        lines.append(f"- 本轮语音开头会先播报：{speech_prefix.strip()}")
    snapshot_line = selected_update.get("snapshot_line")
    if isinstance(snapshot_line, str) and snapshot_line.strip():
        lines.append(f"- 当前项目进度：{snapshot_line.strip()}")
    chapter_lines = selected_update.get("chapter_lines")
    if isinstance(chapter_lines, list):
        for line in [str(item).strip() for item in chapter_lines if str(item).strip()][:3]:
            lines.append(f"- 章节状态：{line}")
    if has_speech_prefix:
        lines.append(
            "使用规则：上面的语音开头会由系统先播放；你不要重复同一条进度、数字或章节名，只需要自然承接，然后继续访谈。"
        )
    else:
        lines.append(
            "使用规则：调试阶段每次回应都尽量自然带一句进度鼓励或后台整理结果；"
            "只说一句，不要堆数字，不要让进度提醒替代追问。"
        )
    return "\n".join(lines)


def _automation_event_progress_update(
    events: list[MemoirAutomationEventResponse],
) -> dict[str, object] | None:
    for event in events:
        summary = event.summary_text.strip()
        if not summary:
            continue
        status = event.status.value if hasattr(event.status, "value") else str(event.status)
        event_type = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if status == MemoirAutomationEventStatus.FAILED.value:
            return {
                "kind": "automation_event_failure",
                "source": "automation_event",
                "text": summary[:120],
                "event_ids": [event.id],
                "event_type": event_type,
                "event_status": status,
                "chapter_id": event.chapter_id,
                "utterance_ids": list(event.utterance_ids),
            }
        if status not in {
            MemoirAutomationEventStatus.COMPLETED.value,
            MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        }:
            continue
        return {
            "kind": "automation_event",
            "source": "automation_event",
            "text": summary[:120],
            "event_ids": [event.id],
            "event_type": event_type,
            "event_status": status,
            "chapter_id": event.chapter_id,
            "utterance_ids": list(event.utterance_ids),
        }
    return None


def _sentence_text(text: str, limit: int = 90) -> str:
    clean = re.sub(r"\s+", "", text).strip()
    if not clean:
        return ""
    if len(clean) > limit:
        clean = clean[:limit].rstrip("，、；：") + "..."
    if clean[-1] not in "。！？!?":
        clean += "。"
    return clean


def _progress_speech_prefix(update: dict[str, object]) -> str:
    if update.get("source") != "automation_event":
        return ""

    kind = str(update.get("kind") or "")
    event_type = str(update.get("event_type") or "")
    text = str(update.get("text") or "").strip()
    if not text:
        return ""

    if kind == "automation_event_failure":
        return _sentence_text(text)
    if event_type in {item.value for item in MemoirAutomationEventType}:
        return _sentence_text(text)
    return ""


def _snapshot_milestone_progress_update(
    snapshot: MemoirProjectProgressSnapshotResponse,
) -> dict[str, object] | None:
    if _is_progress_milestone(snapshot.assigned_utterance_count):
        return {
            "kind": "assigned_utterance_milestone",
            "source": "progress_snapshot",
            "text": f"已经有 {snapshot.assigned_utterance_count} 条讲述记录收进章节。",
            "assigned_utterance_count": snapshot.assigned_utterance_count,
            "valid_utterance_count": snapshot.valid_utterance_count,
            "pending_utterance_count": snapshot.pending_utterance_count,
        }
    if _is_progress_milestone(snapshot.valid_utterance_count):
        return {
            "kind": "valid_utterance_milestone",
            "source": "progress_snapshot",
            "text": f"这个项目已经留下 {snapshot.valid_utterance_count} 条讲述记录。",
            "valid_utterance_count": snapshot.valid_utterance_count,
            "assigned_utterance_count": snapshot.assigned_utterance_count,
            "pending_utterance_count": snapshot.pending_utterance_count,
        }
    return None


def _snapshot_progress_update(snapshot: MemoirProjectProgressSnapshotResponse) -> dict[str, object] | None:
    milestone = _snapshot_milestone_progress_update(snapshot)
    if milestone:
        return _with_snapshot_facts(milestone, snapshot)
    if snapshot.valid_utterance_count <= 0:
        return _with_snapshot_facts(
            {
                "kind": "project_progress_snapshot",
                "source": "progress_snapshot",
                "text": "这个项目还没有留下讲述记录，今天可以从第一条开始。",
            },
            snapshot,
        )
    return _with_snapshot_facts(
        {
            "kind": "project_progress_snapshot",
            "source": "progress_snapshot",
            "text": _snapshot_summary_text(snapshot),
        },
        snapshot,
    )


def _with_snapshot_facts(
    update: dict[str, object],
    snapshot: MemoirProjectProgressSnapshotResponse,
) -> dict[str, object]:
    enriched = dict(update)
    enriched.update(
        {
            "valid_utterance_count": snapshot.valid_utterance_count,
            "assigned_utterance_count": snapshot.assigned_utterance_count,
            "pending_utterance_count": snapshot.pending_utterance_count,
            "chapter_count": snapshot.chapter_count,
            "confirmed_chapter_count": snapshot.confirmed_chapter_count,
            "draft_word_count": snapshot.draft_word_count,
            "source_char_count": snapshot.source_char_count,
            "snapshot_line": _snapshot_facts_line(snapshot),
            "chapter_lines": _chapter_progress_lines(snapshot),
        }
    )
    return enriched


def _snapshot_summary_text(snapshot: MemoirProjectProgressSnapshotResponse) -> str:
    if snapshot.assigned_utterance_count > 0:
        return (
            f"这个项目已经有 {snapshot.assigned_utterance_count} 条讲述收入章节，"
            f"还有 {snapshot.pending_utterance_count} 条待整理。"
        )
    return f"这个项目已经留下 {snapshot.valid_utterance_count} 条讲述记录，接下来可以慢慢整理成章节。"


def _snapshot_facts_line(snapshot: MemoirProjectProgressSnapshotResponse) -> str:
    return (
        f"{snapshot.valid_utterance_count} 条讲述记录，"
        f"{snapshot.assigned_utterance_count} 条已收入章节，"
        f"{snapshot.pending_utterance_count} 条待整理；"
        f"{snapshot.chapter_count} 个章节，"
        f"{snapshot.confirmed_chapter_count} 个已确认；"
        f"讲述原文约 {snapshot.source_char_count} 字，"
        f"章节稿约 {snapshot.draft_word_count} 字。"
    )


def _chapter_progress_lines(snapshot: MemoirProjectProgressSnapshotResponse) -> list[str]:
    lines: list[str] = []
    for chapter in snapshot.chapter_progress_items:
        parts = [
            f"《{chapter.title}》",
            f"{chapter.source_count} 条素材",
        ]
        if chapter.pending_source_count:
            parts.append(f"{chapter.pending_source_count} 条待补写")
        if chapter.removed_source_count:
            parts.append(f"{chapter.removed_source_count} 条素材已移除待更新")
        if chapter.draft_word_count:
            parts.append(f"正文约 {chapter.draft_word_count} 字")
        lines.append("，".join(parts))
    return lines


def _is_progress_milestone(count: int) -> bool:
    return count in PROGRESS_UTTERANCE_MILESTONES or (count >= 150 and count % 50 == 0)


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


def _has_user_message(messages: list[MemoirChatMessage]) -> bool:
    return any(
        message.role == MemoirChatMessageRole.USER.value and message.content_text.strip() for message in messages
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


def _strip_speaker_label_prefix(text: str) -> str:
    labels = "|".join(re.escape(label) for label in SPEAKER_LABELS)
    return re.sub(rf"^\s*(?:{labels})\s*[:：]\s*", "", text, count=1).strip()


def strip_streaming_speaker_label_prefix(text: str) -> tuple[str, bool]:
    """Return sanitized initial LLM text and whether the prefix is resolved.

    Streaming LLM chunks may split a speaker label across chunks, such as
    "小" + "余：嗯". Hold ambiguous prefixes until we can decide whether to
    strip a label or emit normal speech.
    """
    raw = str(text or "")
    if not raw:
        return "", False
    stripped = raw.lstrip()
    if not stripped:
        return "", False
    cleaned = _strip_speaker_label_prefix(raw)
    if cleaned != raw.strip():
        return cleaned, True
    compact = re.sub(r"\s+", "", stripped)
    for label in SPEAKER_LABELS:
        if label in {"AI", "Ai", "ai"}:
            variants = ("AI:", "AI：", "Ai:", "Ai：", "ai:", "ai：")
        else:
            variants = (f"{label}:", f"{label}：")
        if any(variant.startswith(compact) for variant in variants):
            return "", False
    return raw, True


def _clean_speech_text(value: object) -> str:
    text = str(value or "").strip().strip('"“”').strip()
    return _strip_speaker_label_prefix(text)


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
            payload.get("speech_text") or payload.get("text") or payload.get("follow_up_text") or payload.get("message")
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
