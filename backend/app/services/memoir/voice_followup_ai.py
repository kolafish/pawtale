from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from datetime import datetime
import logging
import re

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirChatMessageRole,
    MemoirConversationAction,
    MemoirSessionGoal,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.schemas.memoir_voice import MemoirAutomationEventResponse, MemoirProjectProgressSnapshotResponse


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "你是一位中文回忆录语音访谈主持人。"
    "你的任务不是审问用户，而是帮助叙述者自然地讲出人生经历、人物、事情、地点、时间和感受。"
    "称呼叙述者用“您”。"
)
MAX_CONTEXT_MESSAGES = 8
MAX_CONTEXT_CHARS = 4000
FOLLOW_UP_MAX_TOKENS = 260
GUIDED_FOLLOW_UP_MAX_TOKENS = 320
OPENING_MAX_TOKENS = 420
PROGRESS_PROMPT_VERSION = "memoir_voice_progress_context_v4"
SPEAKER_LABELS = ("小余", "老余", "AI", "Ai", "ai", "助手", "主持人")


INTERVIEWER_TURN_OUTPUT_RULES = """输出协议：第一行只输出 [listen]、[pause] 或 [end] 中的一个标记，第二行起输出中文口语正文。标记不播放。
先判断用户最新一句对“本次讲述”的意图，再生成与之相符的回应，判断顺序如下：
1. 用户表示本次不再讲、今天不讲了、下次/改天再聊、要离开，选 [end]。不要求出现“结束”两个字；没有说稍等就回来继续的“不说了”也属于结束。正文简短告别，不再提问或邀请立即继续，不提前宣称保存成功。
2. 用户明确要求暂停收音，或要暂时离开处理别的事再回来，选 [pause]。仅仅边讲边回想、找词、说“我想想”不等于要求暂停收音，此时选 [listen]，安静承接。正文简短回应暂停，不提问、不催促，可说明准备好后点继续说话。
3. 其余选 [listen]：故事讲完、换话题、记不清某事、只拒绝某话题，或让你少问而他继续讲。正文顺着他的意愿接话。
以上只判断用户对当前对话的意图：过去发生的结束、引用别人说话、否定的结束或已改口为继续，都不是要停止本次访谈。只说故事讲完或“就这些”没有要求停止访谈时仍为 [listen]。
暂停与结束优先于资料补问、人物问题、话题建议和其他访谈策略。标记必须与口语一致；正文中不得出现控制标记、JSON、Markdown 或说话人标签。"""


@dataclass(frozen=True)
class InterviewerTurn:
    primary_intent: str
    intent_tags: list[str]
    speech_text: str
    conversation_action: MemoirConversationAction

    @property
    def expects_answer(self) -> bool:
        return self.conversation_action == MemoirConversationAction.LISTEN


@dataclass(frozen=True)
class InterviewMessage:
    role: str
    content_text: str
    turn_index: int
    id: str
    created_at: datetime


@dataclass(frozen=True)
class InterviewDialogue:
    session_goal: str
    messages: tuple[InterviewMessage, ...]


Message = MemoirChatMessage | InterviewMessage


@dataclass(frozen=True)
class ProgressPromptContext:
    prompt_text: str
    progress_update: dict[str, object] | None = None
    automation_event_ids: list[str] | None = None
    voice_announcement_event_ids: list[str] | None = None


@dataclass(frozen=True)
class InterviewerPersona:
    host_name: str
    voice_gender: str


def interviewer_persona_for_voice(voice: str | None) -> InterviewerPersona:
    normalized = (voice or "").strip().lower()
    if "female" in normalized or "xiaohe" in normalized:
        return InterviewerPersona(host_name="小余", voice_gender="female")
    if "male" in normalized or "m191" in normalized:
        return InterviewerPersona(host_name="小余", voice_gender="male")
    return InterviewerPersona(host_name="小余", voice_gender="unknown")


def system_prompt_for_persona(persona: InterviewerPersona) -> str:
    return f"{SYSTEM_PROMPT}你在对话中自称“{persona.host_name}”。"


def build_progress_prompt_context(snapshot: MemoirProjectProgressSnapshotResponse) -> ProgressPromptContext:
    selected_update = _automation_event_progress_update(snapshot.recent_automation_events)
    if selected_update is not None:
        selected_update = _with_snapshot_facts(selected_update, snapshot)

    if not selected_update:
        return ProgressPromptContext(
            prompt_text=(
                "项目进度事实：本轮没有可播报的结构化进度变化。不要编造记录数、章节名、成文字数或自动整理结果。"
            ),
            progress_update=None,
            automation_event_ids=[],
            voice_announcement_event_ids=[],
        )

    event_ids = selected_update.get("event_ids")
    automation_event_ids = [str(item) for item in event_ids] if isinstance(event_ids, list) else []
    prompt_text = _progress_prompt_text(selected_update)
    return ProgressPromptContext(
        prompt_text=prompt_text,
        progress_update=selected_update,
        automation_event_ids=automation_event_ids,
        voice_announcement_event_ids=automation_event_ids,
    )


def _progress_prompt_text(selected_update: dict[str, object]) -> str:
    lines = [
        "项目进度事实（只允许引用以下事实，不要编造数字、章节名或自动整理结果）：",
        f"- 本轮进展：{selected_update['text']}",
    ]
    snapshot_line = selected_update.get("snapshot_line")
    if isinstance(snapshot_line, str) and snapshot_line.strip():
        lines.append(f"- 当前项目进度：{snapshot_line.strip()}")
    chapter_lines = selected_update.get("chapter_lines")
    if isinstance(chapter_lines, list):
        for line in [str(item).strip() for item in chapter_lines if str(item).strip()][:3]:
            lines.append(f"- 章节状态：{line}")
    lines.append(
        "使用规则：请把进度与本轮需要的问候、承接或问题统一组织成一段自然口语；"
        "不要把进度单独播报，不要重复同一事实，也不要堆砌数字。"
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


def _ordered_messages(messages: Sequence[Message]) -> list[Message]:
    return sorted(messages, key=lambda message: (message.turn_index, message.created_at, message.id))


def _message_context(messages: Sequence[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        content = message.content_text.strip()
        if not content:
            continue
        speaker = "叙述者" if message.role == MemoirChatMessageRole.USER.value else "AI"
        lines.append(f"{speaker}：{content}")
    return "\n".join(lines)


def recent_context_messages(messages: Sequence[Message]) -> list[Message]:
    """Return the latest messages within both the count and character budgets."""
    ordered = _ordered_messages(messages)[-MAX_CONTEXT_MESSAGES:]
    selected: list[Message] = []
    remaining_chars = MAX_CONTEXT_CHARS
    for message in reversed(ordered):
        content = message.content_text.strip()
        if not content:
            continue
        rendered = _message_context((message,))
        separator = 1 if selected else 0
        available = remaining_chars - separator
        if available <= 0:
            break
        if len(rendered) <= available:
            selected.append(message)
            remaining_chars -= separator + len(rendered)
            continue
        speaker = "叙述者：" if message.role == MemoirChatMessageRole.USER.value else "AI："
        content_budget = max(0, available - len(speaker))
        if content_budget:
            if len(content) > content_budget:
                if content_budget <= 9:
                    content = content[:content_budget]
                else:
                    head_budget = max(1, content_budget // 2)
                    tail_budget = content_budget - head_budget - 9
                    content = content[:head_budget] + "……中间省略……" + content[-tail_budget:]
            selected.append(
                InterviewMessage(
                    role=message.role,
                    content_text=content[:content_budget],
                    turn_index=message.turn_index,
                    id=message.id,
                    created_at=message.created_at,
                )
            )
        break
    return list(reversed(selected))


def _recent_context(messages: Sequence[Message]) -> str:
    return _message_context(recent_context_messages(messages)) or "暂无对话。"


def related_context_block(
    current_messages: Sequence[Message],
    related_messages: Sequence[Message],
) -> str:
    """Format topic-linked history that fills an otherwise short current session."""
    current = recent_context_messages(current_messages)
    remaining = max(0, MAX_CONTEXT_MESSAGES - len(current))
    if not remaining:
        return ""
    current_chars = len(_message_context(current))
    if current_chars >= MAX_CONTEXT_CHARS:
        return ""
    selected: list[Message] = []
    chars = current_chars
    for message in list(related_messages)[-remaining:]:
        content = message.content_text.strip()
        if not content:
            continue
        rendered = _message_context((message,))
        extra = len(rendered) + (1 if selected else 0)
        if chars + extra > MAX_CONTEXT_CHARS:
            continue
        selected.append(message)
        chars += extra
    if not selected:
        return ""
    return "上一轮同一话题的历史会话（仅用于承接，不代表当前会话）：\n" + _message_context(selected)


def _has_user_message(messages: Sequence[Message]) -> bool:
    return any(
        message.role == MemoirChatMessageRole.USER.value and message.content_text.strip() for message in messages
    )


def _llm_max_tokens(
    voice_session: MemoirVoiceSession | InterviewDialogue,
    *,
    has_user_message: bool,
    regenerate: bool,
) -> int:
    if not has_user_message:
        return OPENING_MAX_TOKENS
    if regenerate or voice_session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value:
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


def split_interviewer_control_prefix(text: str) -> tuple[str, MemoirConversationAction | None]:
    """Buffer only the control prefix; never send it to TTS or the transcript."""
    text = text.lstrip()
    for action in MemoirConversationAction:
        prefix = f"[{action.value}]"
        if text.startswith(prefix):
            return text[len(prefix):].lstrip(), action
        if prefix.startswith(text):
            return "", None
    if not text.startswith(("[", "{", "`")):
        # A usable reply without an action is a protocol omission, not an end
        # signal. Preserve speech, then wait for the user's explicit resumption.
        logger.warning("Interviewer reply omitted control prefix; defaulting to pause")
        return text, MemoirConversationAction.PAUSE
    raise ValueError("AI 访谈回复缺少有效的会话动作，请重试")
