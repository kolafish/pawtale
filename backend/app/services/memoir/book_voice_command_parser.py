from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import (
    MemoirBookVoiceCommandBookContext,
    MemoirBookVoiceCommandOperationResponse,
    MemoirBookVoiceCommandParseResponse,
    MemoirBookVoiceCommandParseRequest,
    MemoirBookVoiceCommandUncoveredSourceContext,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.book_plan_sources import (
    find_chapter_by_id as _chapter_by_id,
    sorted_chapters as _sorted_chapters,
)


logger = logging.getLogger(__name__)

BOOK_VOICE_COMMAND_SYSTEM_PROMPT = (
    "你是回忆录成书页面的语音编辑助手。你的任务是理解用户口语化的中文语音请求，"
    "把可执行修改解析成安全的操作预览，把编辑判断类问题解析成不会改动数据的建议回复，"
    "也要能回答当前成书状态、可修改范围和上下文追问。"
    "只输出 JSON，不要解释。"
)

BOOK_VOICE_COMMAND_USER_PROMPT_TEMPLATE = """请解析下面这句成书语音指令，输出严格 JSON。

当前章节上下文：
{chapter_lines}

当前成书状态：
{book_status}

当前素材覆盖：
{source_coverage_status}

当前正在编辑章节：
{current_chapter_line}

最近语音对话：
{conversation_lines}

用户原句：
{text}

JSON 格式：
{{
  "intent": "read_outline | read_chapter | read_uncovered_source | rename_chapter | merge_chapters | move_chapter | assign_source | split_chapter | rewrite_chapter | polish_chapter | confirm_chapter | review_chapter_title | review_chapter_length | review_chapter_structure | answer_book_question | capability_help | clarify_previous | unknown",
  "response_kind": "action_preview | consultation | answer | clarification",
  "chapter_ref": "用户提到的章节编号或标题；没有则 null",
  "target_chapter_ref": "合并目标章节编号或标题；没有则 null",
  "source_ref": "用户提到的待收录记录编号、语音记录编号或刚才这条；没有则 null",
  "new_title": "改名后的新标题；没有则 null",
  "direction": "up | down | null",
  "summary": "用一句话说明识别出的操作或问题",
  "reply_text": "建议类问题必须给自然、具体、可直接播报的中文回复；操作类没有则 null"
}}

规则：
- 只根据用户原句和当前章节上下文解析，不要发散新增人物或内容。
- “读目录/念目录/听目录”用 read_outline。
- “读第一章/念某章”用 read_chapter。
- “把第一章改名为童年往事/标题改成……”用 rename_chapter，并提取 new_title。
- “把第一章和第二章合并/把第一章合并到第二章”用 merge_chapters。
- “把某章上移/下移/往前/往后”用 move_chapter，并填 direction。
- “把第二章和第三章顺序换一下/交换第二章和第三章”这类相邻章节交换也用 move_chapter：把前一章下移，或把后一章上移。
- “把第 1 条待收录记录归到第二章/把刚才这条放到职场那章”用 assign_source，source_ref 填记录编号或“刚才这条”，target_chapter_ref 填目标章节。
- “先念一条待收录记录/下一条未收入记录/念一下第一条没收入的记录”用 read_uncovered_source，source_ref 填“第几条”或“下一条”，reply_text 要读出对应待收录记录摘录，并提示用户说“把这条归到哪一章”。
- “第一章是不是需要换个名字/标题好不好/题目是否合适”用 review_chapter_title，不要生成 rename_chapter。
- “第一章是不是太短/太长/字数够不够/篇幅是否合适”用 review_chapter_length，不要生成 rewrite_chapter。
- “这一章结构是否合理/要不要拆分或合并/顺序是否合适”用 review_chapter_structure，除非用户明确要求执行。
- “现在有几章/都有几章/目录里有什么/现在都有哪些章节”等状态问题用 answer_book_question，reply_text 要直接回答章节数量和标题。
- “还有多少记录未收入章节/是不是有记录还没归到章节”等素材覆盖问题用 answer_book_question，reply_text 要按当前素材覆盖回答。
- “我可以修改什么/能改哪些/可以怎么改”等能力问题用 capability_help，reply_text 要结合当前目录说明可做的修改。
- “那个/这个/现在呢/继续”等短追问要结合最近语音对话；能判断指代就继续回答，不能判断用 clarify_previous 并问一个具体澄清问题。
- 建议类 intent 不产生 operations，不需要确认；reply_text 要给判断理由和下一步建议，控制在 120 字以内。
- read_uncovered_source、answer_book_question、capability_help、clarify_previous 不产生 operations，不需要确认。
- 如果缺少必要章节或无法判断，intent 用 unknown。
"""

SUPPORTED_INTENTS = {
    "read_outline",
    "read_chapter",
    "read_uncovered_source",
    "rename_chapter",
    "merge_chapters",
    "move_chapter",
    "assign_source",
    "split_chapter",
    "rewrite_chapter",
    "polish_chapter",
    "confirm_chapter",
    "review_chapter_title",
    "review_chapter_length",
    "review_chapter_structure",
    "answer_book_question",
    "capability_help",
    "clarify_previous",
    "unknown",
}

INTENT_LABELS = {
    "read_outline": "朗读目录",
    "read_chapter": "朗读章节",
    "read_uncovered_source": "朗读记录",
    "rename_chapter": "修改章名",
    "merge_chapters": "合并章节",
    "move_chapter": "移动章节",
    "assign_source": "归入记录",
    "split_chapter": "拆分章节",
    "rewrite_chapter": "重写章节",
    "polish_chapter": "润色章节",
    "confirm_chapter": "确认章节",
    "review_chapter_title": "章名建议",
    "review_chapter_length": "篇幅建议",
    "review_chapter_structure": "结构建议",
    "answer_book_question": "成书问答",
    "capability_help": "修改帮助",
    "clarify_previous": "需要澄清",
    "unknown": "需要再说明",
}

READ_INTENTS = {"read_outline", "read_chapter"}
CONSULTATION_INTENTS = {"review_chapter_title", "review_chapter_length", "review_chapter_structure"}
ANSWER_INTENTS = {"read_uncovered_source", "answer_book_question", "capability_help"}
CLARIFICATION_INTENTS = {"clarify_previous", "unknown"}
NON_CONFIRMATION_INTENTS = READ_INTENTS | CONSULTATION_INTENTS | ANSWER_INTENTS | CLARIFICATION_INTENTS
EXECUTABLE_MUTATION_INTENTS = {"rename_chapter", "move_chapter", "assign_source"}
CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
CHAPTER_NUMBER_RE = re.compile(r"第?\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:章|章节)?")
STRICT_CHAPTER_REF_RE = re.compile(r"第\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:章|章节)")
RENAME_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:的)?(?:章名|标题)?(?:改名为|改成|改为|叫)(.{2,40})")
MERGE_INTO_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:合并到|并到|放到)(.{1,40})")
MERGE_WITH_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:和|跟|与)(.{1,40}?)(?:合并|并到一起|放到一起)")
MOVE_RE = re.compile(r"(?:把|将)?(.{1,40}?)(上移|下移|往前|往后|前移|后移)")
ASSIGN_SOURCE_RE = re.compile(r"(?:把|将)?(.{0,40}?)(?:归到|归入|收入|收录到|放到|分到|归类到)(.{1,40})")
CHAPTER_WORD_ASR_ORDINAL_RE = re.compile(r"第\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*张")
CHAPTER_WORD_ASR_BARE_RE = re.compile(
    r"([0-9]{1,2}|[一二两三四五六七八九十])\s*张(?=(?:和|跟|与|的|换|调|移|顺序|章节|章|[，。,.!！?？、\s]|$))"
)
TITLE_REVIEW_KEYWORDS = ("换个名字", "改个名字", "名字", "标题", "题目", "章名")
LENGTH_REVIEW_KEYWORDS = ("太短", "太长", "字数", "长度", "篇幅", "内容少", "内容多", "够不够")
STRUCTURE_REVIEW_KEYWORDS = ("结构", "顺序", "节奏", "拆分", "合并", "拆开", "放一起")
QUESTION_KEYWORDS = ("是不是", "是否", "要不要", "需不需要", "需要", "合不合适", "好不好", "好吗", "怎么样", "够不够")
CAPABILITY_KEYWORDS = (
    "可以修改什么",
    "可以修改些啥",
    "可以改什么",
    "可以改些啥",
    "可以改点啥",
    "可以改点什么",
    "还可以改什么",
    "还可以改些啥",
    "还可以改点啥",
    "还可以改点什么",
    "还可以修改什么",
    "还可以修改些啥",
    "能修改什么",
    "能修改些啥",
    "能改什么",
    "能改些啥",
    "还能改什么",
    "还能改些啥",
    "还能改点啥",
    "还能改点什么",
    "还能修改什么",
    "还能修改些啥",
    "改点啥",
    "改点什么",
    "改哪些",
    "修改哪些",
    "怎么修改",
    "怎么改",
    "能做什么",
    "可以做什么",
    "会做什么",
)
CHAPTER_COUNT_KEYWORDS = (
    "几章",
    "几张",
    "几个章",
    "几个章节",
    "多少章",
    "多少章节",
    "目录有几",
    "现在有几",
    "现在都有几",
    "一共有几",
    "总共有几",
    "章节有几",
    "章有几个",
)
OUTLINE_STATUS_KEYWORDS = ("有哪些章节", "都有哪些章", "现在有什么章", "目录里有什么", "目录是什么", "现在目录")
SOURCE_COVERAGE_KEYWORDS = (
    "未收入",
    "未收录",
    "未归入",
    "未归到",
    "未归类",
    "没收入",
    "没收录",
    "没归入",
    "没归到",
    "没归类",
    "还没收入",
    "还没收录",
    "还没归入",
    "还没归到",
    "还没归类",
)
SOURCE_COVERAGE_OBJECT_KEYWORDS = ("记录", "素材", "讲述", "语音", "转写")
SOURCE_READ_KEYWORDS = (
    "念一条",
    "读一条",
    "念一下",
    "读一下",
    "念下",
    "读下",
    "给我念",
    "给我读",
    "先念",
    "先读",
    "下一条",
    "下一个",
    "一条一条",
    "逐条",
)
SOURCE_ASSIGN_KEYWORDS = ("归到", "归入", "收入", "收录到", "放到", "分到", "归类到")
CURRENT_SOURCE_REFS = ("这条", "这一条", "刚才这条", "刚念的", "刚读的", "它", "这个")
CHAPTER_SWAP_KEYWORDS = (
    "顺序换",
    "顺序调换",
    "顺序交换",
    "交换顺序",
    "换一下",
    "换一换",
    "互换",
    "对调",
    "调换",
    "换成",
)
AMBIGUOUS_FOLLOW_UPS = {"那个", "这个", "那", "嗯", "现在呢", "然后呢", "继续", "就是那个", "刚才那个", "前面那个"}


@dataclass(frozen=True)
class ParsedCommandPayload:
    intent: str
    chapter_ref: str | None = None
    target_chapter_ref: str | None = None
    source_ref: str | None = None
    new_title: str | None = None
    direction: str | None = None
    summary: str | None = None
    reply_text: str | None = None
    used_ai: bool = False
    provider: str | None = None
    model: str | None = None


async def parse_payload_with_ai(
    req: MemoirBookVoiceCommandParseRequest,
    plan: MemoirBookPlan,
    *,
    ai_client: AITextClient | None = None,
) -> ParsedCommandPayload:
    fallback = _fallback_payload(
        req.text,
        plan=plan,
        conversation_context=req.conversation_context,
        book_context=req.book_context,
    )
    normalized_text = _compact(_normalize_chapter_word_text(req.text))
    if fallback.intent == "read_uncovered_source" and _is_source_read_request(normalized_text):
        return fallback
    if _is_resolved_local_action(fallback, plan, req.current_chapter_id, req.book_context):
        return fallback
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(req.ai_model)
        content = await client._call_llm(
            BOOK_VOICE_COMMAND_SYSTEM_PROMPT,
            BOOK_VOICE_COMMAND_USER_PROMPT_TEMPLATE.format(
                chapter_lines=_chapter_prompt_lines(plan),
                book_status=_book_status_prompt_line(plan),
                source_coverage_status=_source_coverage_prompt_line(req.book_context),
                current_chapter_line=_current_chapter_prompt_line(plan, req.current_chapter_id),
                conversation_lines=_conversation_prompt_lines(req.conversation_context),
                text=_normalize_chapter_word_text(req.text),
            ),
            temperature=0.1,
            max_tokens=700,
            timeout=45.0,
        )
        payload = extract_json_payload(str(content))
        parsed = _payload_from_json(payload)
        if parsed.intent == "unknown":
            return fallback
        if (
            parsed.intent in {"move_chapter", "assign_source"}
            and fallback.intent == parsed.intent
            and _is_resolved_local_action(fallback, plan, req.current_chapter_id, req.book_context)
        ):
            return fallback
        if parsed.intent == "move_chapter" and not parsed.direction and fallback.intent == "move_chapter":
            return fallback
        reply_text = parsed.reply_text
        if parsed.intent == "clarify_previous" and not reply_text:
            reply_text = _clarification_reply_text(req.conversation_context, None)
        return ParsedCommandPayload(
            intent=parsed.intent,
            chapter_ref=parsed.chapter_ref,
            target_chapter_ref=parsed.target_chapter_ref,
            source_ref=parsed.source_ref,
            new_title=parsed.new_title,
            direction=parsed.direction,
            summary=parsed.summary,
            reply_text=reply_text,
            used_ai=True,
            provider=getattr(client, "provider_name", None),
            model=getattr(client, "model_name", req.ai_model),
        )
    except Exception:
        logger.warning(
            "AI book voice command parse failed provider=%s model=%s; using fallback",
            getattr(client, "provider_name", "unknown"),
            getattr(client, "model_name", req.ai_model or "default"),
            exc_info=True,
        )
        return fallback


def _payload_from_json(payload: Any) -> ParsedCommandPayload:
    if not isinstance(payload, dict):
        return ParsedCommandPayload(intent="unknown")
    intent = str(payload.get("intent") or "").strip()
    if intent not in SUPPORTED_INTENTS:
        intent = "unknown"
    direction = _normalized_direction(payload.get("direction"))
    return ParsedCommandPayload(
        intent=intent,
        chapter_ref=_optional_text(payload.get("chapter_ref")),
        target_chapter_ref=_optional_text(payload.get("target_chapter_ref")),
        source_ref=_optional_text(payload.get("source_ref")),
        new_title=_optional_text(payload.get("new_title")),
        direction=direction,
        summary=_optional_text(payload.get("summary")),
        reply_text=_clean_reply_text(payload.get("reply_text")),
    )


def _is_resolved_local_action(
    payload: ParsedCommandPayload,
    plan: MemoirBookPlan,
    current_chapter_id: str | None,
    book_context: MemoirBookVoiceCommandBookContext | None,
) -> bool:
    chapters = _sorted_chapters(plan)
    if payload.intent == "move_chapter":
        return (
            payload.direction in {"up", "down"}
            and _resolve_chapter(chapters, payload.chapter_ref, current_chapter_id) is not None
        )
    if payload.intent == "assign_source":
        chapter = _resolve_chapter(chapters, payload.chapter_ref, current_chapter_id)
        target_chapter = _resolve_chapter(chapters, payload.target_chapter_ref, current_chapter_id)
        source = _resolve_uncovered_source(book_context, payload.source_ref, None)
        return (target_chapter is not None or chapter is not None) and source is not None
    return False


def _fallback_payload(
    text: str,
    *,
    plan: MemoirBookPlan,
    conversation_context: list[Any] | None = None,
    book_context: MemoirBookVoiceCommandBookContext | None = None,
) -> ParsedCommandPayload:
    normalized = _compact(_normalize_chapter_word_text(text))
    if not normalized:
        return ParsedCommandPayload(intent="unknown")
    if any(keyword in normalized for keyword in ("读目录", "念目录", "听目录", "朗读目录")):
        return ParsedCommandPayload(intent="read_outline", summary="朗读当前章节目录")

    if _is_source_read_request(normalized):
        source_ref = _source_read_ref(normalized)
        return ParsedCommandPayload(
            intent="read_uncovered_source",
            source_ref=source_ref,
            summary="读出一条待收录讲述记录。",
            reply_text=_source_read_reply_text(book_context, conversation_context, source_ref),
        )

    if _is_source_coverage_question(normalized):
        return ParsedCommandPayload(
            intent="answer_book_question",
            summary="回答当前素材收入章节状态。",
            reply_text=_source_coverage_reply_text(plan, book_context),
        )

    if _is_chapter_count_question(normalized) or any(keyword in normalized for keyword in OUTLINE_STATUS_KEYWORDS):
        return ParsedCommandPayload(
            intent="answer_book_question",
            summary="回答当前成书章节状态。",
            reply_text=_chapter_count_reply_text(plan),
        )

    if any(keyword in normalized for keyword in CAPABILITY_KEYWORDS):
        return ParsedCommandPayload(
            intent="capability_help",
            summary="说明语音编辑可以做的修改。",
            reply_text=_capability_reply_text(plan, None),
        )

    if _is_ambiguous_follow_up(normalized):
        return ParsedCommandPayload(
            intent="clarify_previous",
            summary="需要确认上一句指的是哪一项修改。",
            reply_text=_clarification_reply_text(conversation_context, None),
        )

    consultation_intent = _fallback_consultation_intent(normalized)
    if consultation_intent:
        return ParsedCommandPayload(
            intent=consultation_intent,
            chapter_ref=_fallback_chapter_ref(normalized),
        )

    assign_payload = _fallback_assign_source_payload(normalized, book_context, conversation_context)
    if assign_payload:
        return assign_payload

    swap_payload = _fallback_swap_payload(normalized, plan)
    if swap_payload:
        return swap_payload

    move_match = MOVE_RE.search(normalized)
    if move_match:
        return ParsedCommandPayload(
            intent="move_chapter",
            chapter_ref=move_match.group(1),
            direction=_normalized_direction(move_match.group(2)),
        )

    merge_into = MERGE_INTO_RE.search(normalized)
    if merge_into:
        return ParsedCommandPayload(
            intent="merge_chapters",
            chapter_ref=merge_into.group(1),
            target_chapter_ref=merge_into.group(2),
        )
    merge_with = MERGE_WITH_RE.search(normalized)
    if merge_with:
        return ParsedCommandPayload(
            intent="merge_chapters",
            chapter_ref=merge_with.group(1),
            target_chapter_ref=merge_with.group(2),
        )

    rename = RENAME_RE.search(normalized)
    if rename:
        return ParsedCommandPayload(
            intent="rename_chapter",
            chapter_ref=rename.group(1),
            new_title=_clean_title(rename.group(2)),
        )

    read_match = CHAPTER_NUMBER_RE.search(normalized)
    if read_match and any(keyword in normalized for keyword in ("读", "念", "听", "朗读")):
        return ParsedCommandPayload(intent="read_chapter", chapter_ref=read_match.group(0))

    return ParsedCommandPayload(intent="unknown")


def _fallback_consultation_intent(normalized: str) -> str | None:
    if not any(keyword in normalized for keyword in QUESTION_KEYWORDS):
        return None
    if any(keyword in normalized for keyword in TITLE_REVIEW_KEYWORDS):
        return "review_chapter_title"
    if any(keyword in normalized for keyword in LENGTH_REVIEW_KEYWORDS):
        return "review_chapter_length"
    if any(keyword in normalized for keyword in STRUCTURE_REVIEW_KEYWORDS):
        return "review_chapter_structure"
    return None


def _fallback_chapter_ref(normalized: str) -> str | None:
    if any(keyword in normalized for keyword in ("本章", "这章", "这一章", "当前章", "当前章节")):
        return "本章"
    match = CHAPTER_NUMBER_RE.search(normalized)
    if match:
        return match.group(0)
    return normalized


def _fallback_swap_payload(normalized: str, plan: MemoirBookPlan) -> ParsedCommandPayload | None:
    if not any(keyword in normalized for keyword in CHAPTER_SWAP_KEYWORDS):
        return None
    chapter_refs = [match.group(0) for match in STRICT_CHAPTER_REF_RE.finditer(normalized)]
    if len(chapter_refs) < 2:
        chapter_refs = _title_swap_refs(normalized)
    if len(chapter_refs) < 2:
        return None
    chapters = _sorted_chapters(plan)
    first = _resolve_chapter(chapters, chapter_refs[0], None)
    second = _resolve_chapter(chapters, chapter_refs[1], None)
    if not first or not second or first.id == second.id:
        return None
    first_index = chapters.index(first)
    second_index = chapters.index(second)
    if abs(first_index - second_index) != 1:
        return ParsedCommandPayload(intent="unknown")
    return ParsedCommandPayload(
        intent="move_chapter",
        chapter_ref=chapter_refs[0],
        direction="down" if first_index < second_index else "up",
    )


def _title_swap_refs(normalized: str) -> list[str]:
    if not any(separator in normalized for separator in ("和", "跟", "与")):
        return []
    if not any(keyword in normalized for keyword in CHAPTER_SWAP_KEYWORDS):
        return []
    text = _compact(normalized)
    for separator in ("和", "跟", "与"):
        if separator not in text:
            continue
        left, right = text.split(separator, 1)
        right = re.split(
            r"(这?两(?:章|个章节)|两个章节|的?顺序|顺序|换一下|换一换|互换|对调|调换|交换|顺序换|顺序调换|顺序交换)",
            right,
            maxsplit=1,
        )[0]
        first = _clean_chapter_ref(left)
        second = _clean_chapter_ref(right)
        if first and second:
            return [first, second]
    return []


def _clean_chapter_ref(value: str) -> str:
    text = _compact(value).strip("，。,.!！?？、")
    while text:
        before = text
        text = text.strip("，。,.!！?？、")
        text = re.sub(
            r"^(?:不是啊|不是|我说|我是说|我讲的是|我想|我要|你帮我|帮我|请|麻烦|把|将|那个|这个)",
            "",
            text,
        )
        if text == before:
            break
    return re.sub(
        r"(这?两(?:章|个章节)|两个章节|那一章|这一章|那章|这章|章节|的?顺序|顺序|那个|这个|那|这)$",
        "",
        text,
    ).strip("，。,.!！?？、")


def _is_chapter_count_question(normalized: str) -> bool:
    return any(keyword in normalized for keyword in CHAPTER_COUNT_KEYWORDS)


def _is_source_coverage_question(normalized: str) -> bool:
    return (
        any(keyword in normalized for keyword in SOURCE_COVERAGE_KEYWORDS)
        and any(keyword in normalized for keyword in SOURCE_COVERAGE_OBJECT_KEYWORDS)
    )


def _is_source_read_request(normalized: str) -> bool:
    return (
        any(keyword in normalized for keyword in SOURCE_READ_KEYWORDS)
        and (
            any(keyword in normalized for keyword in SOURCE_COVERAGE_OBJECT_KEYWORDS)
            or any(keyword in normalized for keyword in SOURCE_COVERAGE_KEYWORDS)
            or "条" in normalized
        )
    )


def _source_read_ref(normalized: str) -> str | None:
    if "下一条" in normalized or "下一个" in normalized:
        return "下一条"
    match = re.search(r"第?\s*([0-9]{1,3}|[一二两三四五六七八九十])\s*条", normalized)
    if match:
        return f"待收录第{match.group(1)}条"
    return None


def _fallback_assign_source_payload(
    normalized: str,
    book_context: MemoirBookVoiceCommandBookContext | None,
    conversation_context: list[Any] | None,
) -> ParsedCommandPayload | None:
    if not any(keyword in normalized for keyword in SOURCE_ASSIGN_KEYWORDS):
        return None
    match = ASSIGN_SOURCE_RE.search(normalized)
    if not match:
        return None
    source_ref = _clean_source_ref(match.group(1))
    target_ref = _clean_chapter_ref(match.group(2))
    if not source_ref or source_ref in {"那", "那个", "这个"}:
        source_ref = _latest_source_ref_from_context(conversation_context) or source_ref
    if not target_ref:
        return None
    source = _resolve_uncovered_source(book_context, source_ref, conversation_context)
    if not source:
        return None
    return ParsedCommandPayload(
        intent="assign_source",
        chapter_ref=target_ref,
        target_chapter_ref=target_ref,
        source_ref=f"待收录第{_source_list_position(book_context, source)}条",
    )


def _clean_source_ref(value: str) -> str:
    text = _compact(value).strip("，。,.!！?？、")
    text = re.sub(r"^(把|将|那个|这个|那条|这条)", "", text)
    return text


def _latest_source_ref_from_context(conversation_context: list[Any] | None) -> str | None:
    for item in reversed(conversation_context or []):
        if getattr(item, "role", "") != "assistant":
            continue
        text = _compact(getattr(item, "text", "") or "")
        match = re.search(r"待收录第\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*条", text)
        if match:
            return f"待收录第{match.group(1)}条"
        match = re.search(r"语音记录\s*([0-9]{1,3})", text)
        if match:
            return f"语音记录{match.group(1)}"
    return None


def _resolve_uncovered_source(
    book_context: MemoirBookVoiceCommandBookContext | None,
    source_ref: str | None,
    conversation_context: list[Any] | None,
) -> MemoirBookVoiceCommandUncoveredSourceContext | None:
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    if not sources:
        return None
    ref = _compact(source_ref or "")
    if ref in set(CURRENT_SOURCE_REFS) or not ref:
        ref = _latest_source_ref_from_context(conversation_context) or ref
    number = _source_ref_number(ref)
    if number is not None:
        if "语音记录" in ref or "记录编号" in ref:
            return next((source for source in sources if source.source_index == number), None)
        if 1 <= number <= len(sources):
            return sources[number - 1]
        return next((source for source in sources if source.source_index == number), None)
    return None


def _source_ref_number(ref: str) -> int | None:
    match = re.search(r"([0-9]{1,3}|[一二两三四五六七八九十])", ref)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return CHINESE_NUMBERS.get(raw)


def _source_list_position(
    book_context: MemoirBookVoiceCommandBookContext | None,
    source: MemoirBookVoiceCommandUncoveredSourceContext,
) -> int:
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    for index, item in enumerate(sources, start=1):
        if item.utterance_id == source.utterance_id:
            return index
    return 1


def _is_ambiguous_follow_up(normalized: str) -> bool:
    return normalized in AMBIGUOUS_FOLLOW_UPS or (
        len(normalized) <= 6 and normalized.startswith(("那个", "这个", "刚才", "前面"))
    )


def _response_kind_for_intent(intent: str) -> str:
    if intent in CONSULTATION_INTENTS:
        return "consultation"
    if intent in ANSWER_INTENTS:
        return "answer"
    if intent in CLARIFICATION_INTENTS:
        return "clarification"
    return "action_preview"


def _book_status_prompt_line(plan: MemoirBookPlan) -> str:
    chapters = _sorted_chapters(plan)
    non_empty_drafts = sum(1 for chapter in chapters if _draft_char_count(chapter) > 0)
    return f"当前目录 {len(chapters)} 章，已有正文 {non_empty_drafts} 章，规划状态 {plan.status}。"


def _source_coverage_prompt_line(book_context: MemoirBookVoiceCommandBookContext | None) -> str:
    coverage = book_context.source_coverage if book_context else None
    if not coverage:
        return "未提供最新素材覆盖统计。"
    line = (
        f"讲述记录共 {coverage.total_source_count} 条，"
        f"已收入章节 {coverage.covered_source_count} 条，"
        f"未收入章节 {coverage.uncovered_source_count} 条。"
    )
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    if sources:
        source_lines = []
        for index, source in enumerate(sources[:6], start=1):
            excerpt = _clean_reply_text(source.text) or "无文本"
            source_lines.append(f"待收录第 {index} 条｜语音记录 {source.source_index}｜摘录：{excerpt[:80]}")
        line += "\n待收录记录列表：\n" + "\n".join(source_lines)
    return line


def _conversation_prompt_lines(conversation_context: list[Any] | None) -> str:
    lines: list[str] = []
    for item in (conversation_context or [])[-6:]:
        role = getattr(item, "role", "")
        text = _clean_reply_text(getattr(item, "text", ""))
        if role not in {"user", "assistant"} or not text:
            continue
        label = "用户" if role == "user" else "AI"
        summary = _clean_reply_text(getattr(item, "summary", None))
        if summary:
            lines.append(f"{label}：{text}（{summary}）")
        else:
            lines.append(f"{label}：{text}")
    return "\n".join(lines) or "无"


def _book_question_reply_text(
    original_text: str,
    plan: MemoirBookPlan,
    ai_reply_text: str | None,
    book_context: MemoirBookVoiceCommandBookContext | None,
) -> str:
    normalized = _compact(_normalize_chapter_word_text(original_text))
    cleaned_ai_reply = _clean_reply_text(ai_reply_text)
    if _is_source_read_request(normalized):
        return cleaned_ai_reply or _source_read_reply_text(book_context, None)
    if _is_source_coverage_question(normalized):
        return _source_coverage_reply_text(plan, book_context)
    if cleaned_ai_reply:
        return cleaned_ai_reply
    if _is_chapter_count_question(normalized) or any(keyword in normalized for keyword in OUTLINE_STATUS_KEYWORDS):
        return _chapter_count_reply_text(plan)
    return _chapter_count_reply_text(plan)


def _source_coverage_reply_text(plan: MemoirBookPlan, book_context: MemoirBookVoiceCommandBookContext | None) -> str:
    coverage = book_context.source_coverage if book_context else None
    chapters = _sorted_chapters(plan)
    if coverage:
        if coverage.uncovered_source_count > 0:
            return (
                f"这本书现在共有 {coverage.total_source_count} 条讲述记录，"
                f"已收入章节 {coverage.covered_source_count} 条，"
                f"还有 {coverage.uncovered_source_count} 条未收入章节。"
                "你可以在下方待收录记录里逐条分配，或告诉我把哪条记录放到哪一章。"
            )
        return (
            f"这本书现在共有 {coverage.total_source_count} 条讲述记录，"
            f"都已收入当前 {len(chapters)} 个章节。"
        )
    covered_count = sum(len(chapter.source_refs_json or []) for chapter in chapters)
    return (
        f"我这里还没有拿到最新待收录统计；当前章节里已引用 {covered_count} 条素材。"
        "可以刷新成书页后再问我还有多少记录未收入章节。"
    )


def _source_read_reply_text(
    book_context: MemoirBookVoiceCommandBookContext | None,
    conversation_context: list[Any] | None,
    source_ref: str | None = None,
) -> str:
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    if not sources:
        return "现在没有拿到可逐条处理的待收录记录。你可以先刷新成书页，或到下方待收录记录里查看。"
    last_position = 0
    latest_ref = _latest_source_ref_from_context(conversation_context)
    if latest_ref:
        last_position = _source_ref_number(latest_ref) or 0
    ref = _compact(source_ref or "")
    if ref in {"下一条", "下一个"}:
        next_position = min(last_position + 1, len(sources)) if last_position else 1
    else:
        explicit_position = _source_ref_number(ref)
        next_position = explicit_position if explicit_position and 1 <= explicit_position <= len(sources) else 1
    source = sources[next_position - 1]
    text = (_clean_reply_text(source.text) or "这条暂时没有可读文本")[:140]
    return (
        f"待收录第 {next_position} 条，语音记录 {source.source_index}：{text}。"
        "你可以说，把这条归到第几章，或者说下一条。"
    )


def _chapter_count_reply_text(plan: MemoirBookPlan) -> str:
    chapters = _sorted_chapters(plan)
    if not chapters:
        return "现在还没有章节。你可以先生成目录，再用语音让我读目录、改章名或调整顺序。"
    chapter_names = "，".join(f"第 {index} 章「{chapter.title}」" for index, chapter in enumerate(chapters, start=1))
    return f"现在目录有 {len(chapters)} 章：{chapter_names}。你可以继续问某一章是否太短，或直接说想怎么改。"


def _capability_reply_text(plan: MemoirBookPlan, ai_reply_text: str | None) -> str:
    cleaned_ai_reply = _clean_reply_text(ai_reply_text)
    if cleaned_ai_reply:
        return cleaned_ai_reply
    chapters = _sorted_chapters(plan)
    count_text = f"现在有 {len(chapters)} 章，" if chapters else ""
    return (
        f"{count_text}我可以帮你读目录或读某章、改章名、上移下移章节、合并章节，"
        "也可以判断某章名字是否合适、篇幅是不是太短。真正改动前我会先复述并等你确认。"
    )


def _clarification_reply_text(conversation_context: list[Any] | None, ai_reply_text: str | None) -> str:
    cleaned_ai_reply = _clean_reply_text(ai_reply_text)
    if cleaned_ai_reply:
        return cleaned_ai_reply
    last_assistant_text = ""
    for item in reversed(conversation_context or []):
        if getattr(item, "role", "") != "assistant":
            continue
        last_assistant_text = _clean_reply_text(getattr(item, "text", "")) or ""
        if last_assistant_text:
            break
    if last_assistant_text:
        return "你是接着刚才那条建议，想改章名、调整顺序，还是让我继续读目录？请直接说要改哪一章。"
    return "我还没确定你指的是哪一处。你可以说：现在有几章、第一章是不是太短，或者把第一章改名为什么。"


def _unknown_reply_text(chapters: list[MemoirBookPlanChapter]) -> str:
    if chapters:
        return "我还没听清你想改哪一块。你可以问现在有几章、第一章是不是太短，或直接说把第一章改名为什么。"
    return "我还没听清。现在还没有章节，可以先生成目录，再让我读目录或调整章节。"


def response_from_payload(
    original_text: str,
    plan: MemoirBookPlan,
    current_chapter_id: str | None,
    payload: ParsedCommandPayload,
    book_context: MemoirBookVoiceCommandBookContext | None = None,
) -> MemoirBookVoiceCommandParseResponse:
    display_text = _normalize_chapter_word_text(original_text).strip()
    intent = payload.intent if payload.intent in SUPPORTED_INTENTS else "unknown"
    chapters = _sorted_chapters(plan)
    chapter = _resolve_chapter(chapters, payload.chapter_ref, current_chapter_id)
    target_chapter = _resolve_chapter(chapters, payload.target_chapter_ref, current_chapter_id)
    source = _resolve_uncovered_source(book_context, payload.source_ref, None)

    operations: list[MemoirBookVoiceCommandOperationResponse] = []
    if intent == "read_outline":
        operations.append(MemoirBookVoiceCommandOperationResponse(type="read_outline"))
    elif intent in {"read_chapter", "rewrite_chapter", "polish_chapter", "confirm_chapter", "split_chapter"}:
        if chapter:
            operations.append(_operation(intent, chapter=chapter))
        else:
            intent = "unknown"
    elif intent == "rename_chapter":
        title = _clean_title(payload.new_title or "")
        if chapter and title:
            operations.append(_operation(intent, chapter=chapter, new_title=title))
        else:
            intent = "unknown"
    elif intent == "merge_chapters":
        if chapter and target_chapter and chapter.id != target_chapter.id:
            operations.append(_operation(intent, chapter=chapter, target_chapter=target_chapter))
        else:
            intent = "unknown"
    elif intent == "move_chapter":
        direction = payload.direction if payload.direction in {"up", "down"} else None
        if chapter and direction:
            operations.append(_operation(intent, chapter=chapter, direction=direction))
        else:
            intent = "unknown"
    elif intent == "assign_source":
        assignment_chapter = target_chapter or chapter
        if assignment_chapter and source:
            operations.append(_operation(intent, chapter=assignment_chapter, source=source, source_position=_source_list_position(book_context, source)))
        else:
            intent = "unknown"
    elif intent in CONSULTATION_INTENTS:
        if not chapter:
            chapter = _chapter_by_id(chapters, current_chapter_id)
        if chapter or intent == "review_chapter_structure":
            operations = []
        else:
            intent = "unknown"
    elif intent == "read_uncovered_source":
        operations = []
    elif intent in ANSWER_INTENTS or intent == "clarify_previous":
        operations = []

    if intent in CONSULTATION_INTENTS:
        summary = payload.summary or _summary_for_consultation(intent, chapter)
        reply_text = _consultation_reply_text(intent, chapter, chapters, payload.reply_text)
    elif intent == "answer_book_question":
        summary = payload.summary or "回答当前成书章节状态。"
        reply_text = _book_question_reply_text(display_text, plan, payload.reply_text, book_context)
    elif intent == "read_uncovered_source":
        summary = payload.summary or "读出一条待收录讲述记录。"
        reply_text = payload.reply_text or _source_read_reply_text(book_context, None, payload.source_ref)
    elif intent == "capability_help":
        summary = payload.summary or "说明语音编辑可以做的修改。"
        reply_text = _capability_reply_text(plan, payload.reply_text)
    elif intent == "clarify_previous":
        summary = payload.summary or "需要确认上一句指的是哪一项修改。"
        reply_text = _clarification_reply_text(None, payload.reply_text)
    elif intent == "unknown":
        summary = payload.summary or _summary_for_operation(intent, operations)
        reply_text = _unknown_reply_text(chapters)
    else:
        summary = _summary_for_operation(intent, operations)
        reply_text = None

    response_kind = _response_kind_for_intent(intent)
    return MemoirBookVoiceCommandParseResponse(
        original_text=display_text,
        intent=intent,
        intent_label=INTENT_LABELS[intent],
        response_kind=response_kind,
        summary=summary,
        reply_text=reply_text,
        operations=operations,
        requires_confirmation=intent not in NON_CONFIRMATION_INTENTS,
        used_ai=payload.used_ai and intent != "unknown",
        provider=payload.provider if payload.used_ai and intent != "unknown" else None,
        model=payload.model if payload.used_ai and intent != "unknown" else None,
    )


def _operation(
    operation_type: str,
    *,
    chapter: MemoirBookPlanChapter | None = None,
    target_chapter: MemoirBookPlanChapter | None = None,
    source: MemoirBookVoiceCommandUncoveredSourceContext | None = None,
    source_position: int | None = None,
    new_title: str | None = None,
    direction: str | None = None,
) -> MemoirBookVoiceCommandOperationResponse:
    return MemoirBookVoiceCommandOperationResponse(
        type=operation_type,
        chapter_id=chapter.id if chapter else None,
        chapter_title=chapter.title if chapter else None,
        target_chapter_id=target_chapter.id if target_chapter else None,
        target_chapter_title=target_chapter.title if target_chapter else None,
        new_title=new_title,
        direction=direction,
        source_position=source_position if source else None,
        source_index=source.source_index if source else None,
        utterance_id=source.utterance_id if source else None,
    )


def _summary_for_operation(intent: str, operations: list[MemoirBookVoiceCommandOperationResponse]) -> str:
    operation = operations[0] if operations else None
    if intent == "read_outline":
        return "准备朗读当前章节目录。"
    if intent == "read_chapter" and operation:
        return f"准备朗读「{operation.chapter_title}」。"
    if intent == "rename_chapter" and operation:
        return f"准备把「{operation.chapter_title}」改名为「{operation.new_title}」。"
    if intent == "merge_chapters" and operation:
        return f"准备把「{operation.chapter_title}」合并到「{operation.target_chapter_title}」。"
    if intent == "move_chapter" and operation:
        direction = "上移" if operation.direction == "up" else "下移"
        return f"准备把「{operation.chapter_title}」{direction}。"
    if intent == "assign_source" and operation:
        source_position = operation.source_position or operation.source_index or 1
        return f"准备把待收录第 {source_position} 条归入「{operation.chapter_title}」。"
    if intent == "split_chapter" and operation:
        return f"准备拆分「{operation.chapter_title}」。"
    if intent == "rewrite_chapter" and operation:
        return f"准备重写「{operation.chapter_title}」。"
    if intent == "polish_chapter" and operation:
        return f"准备润色「{operation.chapter_title}」。"
    if intent == "confirm_chapter" and operation:
        return f"准备确认「{operation.chapter_title}」。"
    return "还没有听清要修改哪一章，请换个说法再试。"


def _summary_for_consultation(intent: str, chapter: MemoirBookPlanChapter | None) -> str:
    if chapter and intent == "review_chapter_title":
        return f"正在判断「{chapter.title}」的章名是否合适。"
    if chapter and intent == "review_chapter_length":
        return f"正在判断「{chapter.title}」的篇幅是否合适。"
    if chapter and intent == "review_chapter_structure":
        return f"正在判断「{chapter.title}」的结构是否合适。"
    return "正在判断当前目录结构是否合适。"


def _consultation_reply_text(
    intent: str,
    chapter: MemoirBookPlanChapter | None,
    chapters: list[MemoirBookPlanChapter],
    ai_reply_text: str | None,
) -> str:
    cleaned_ai_reply = _clean_reply_text(ai_reply_text)
    if cleaned_ai_reply:
        return cleaned_ai_reply

    if intent == "review_chapter_title" and chapter:
        content = _latest_draft_content(chapter)
        if content:
            return (
                f"「{chapter.title}」能概括这一章，但还偏概括。"
                "如果正文里有更鲜明的人、地点或事件，章名可以换得更具体；你也可以直接说想改成什么。"
            )
        return (
            f"「{chapter.title}」先能表达方向，但现在正文还不多。"
            "建议等这一章生成或补充素材后，再根据最有画面感的场景来定章名。"
        )

    if intent == "review_chapter_length" and chapter:
        char_count = _draft_char_count(chapter)
        average_count = _average_draft_char_count(chapters)
        if char_count <= 0:
            return f"「{chapter.title}」现在还没有正文，暂时不能判断长短。可以先生成这一章，或补充一两段关键回忆。"
        if average_count and char_count < max(180, average_count * 0.65):
            return (
                f"「{chapter.title}」现在约 {char_count} 字，短于其他章节平均约 {average_count} 字。"
                "建议补一个具体场景、人物对话或时间线转折，再考虑进入下一章。"
            )
        if average_count and char_count > average_count * 1.45:
            return (
                f"「{chapter.title}」现在约 {char_count} 字，比其他章节更长。"
                "如果里面有两个明显阶段，可以考虑拆开；否则保持也没问题。"
            )
        return (
            f"「{chapter.title}」现在约 {char_count} 字，篇幅看起来是可以的。"
            "如果你想让它更饱满，优先补一个最有记忆点的细节。"
        )

    if intent == "review_chapter_structure":
        if chapter:
            source_count = len(chapter.source_refs_json or [])
            return (
                f"「{chapter.title}」目前收录 {source_count} 条素材。"
                "结构上可以先按时间、地点或一个核心事件来收束；如果有两条主线，再考虑拆章。"
            )
        return (
            f"现在目录有 {len(chapters)} 章。"
            "整体结构可以先看每章是否只承担一个主题，再调整顺序或合并重复章节。"
        )

    return "我还没判断清楚。你可以直接问：第一章名字合适吗，或者第一章是不是太短了。"


def _chapter_prompt_lines(plan: MemoirBookPlan) -> str:
    lines = []
    for index, chapter in enumerate(_sorted_chapters(plan), start=1):
        content = _latest_draft_content(chapter)
        excerpt = content[:80].replace("\n", " ")
        line = (
            f"{index}. {chapter.title}"
            f"｜素材 {len(chapter.source_refs_json or [])} 条"
            f"｜正文 {_draft_char_count(chapter)} 字"
        )
        if excerpt:
            line += f"｜摘录：{excerpt}"
        lines.append(line)
    return "\n".join(lines) or "暂无章节"


def _current_chapter_prompt_line(plan: MemoirBookPlan, current_chapter_id: str | None) -> str:
    chapters = _sorted_chapters(plan)
    chapter = _chapter_by_id(chapters, current_chapter_id)
    if not chapter:
        return "未指定"
    index = chapters.index(chapter) + 1
    return f"第 {index} 章，{chapter.title}，正文 {_draft_char_count(chapter)} 字"


def _resolve_chapter(
    chapters: list[MemoirBookPlanChapter],
    ref: str | None,
    current_chapter_id: str | None,
) -> MemoirBookPlanChapter | None:
    text = _clean_chapter_ref(ref or "")
    if text in {"本章", "这一章", "当前章", "当前章节"} and current_chapter_id:
        return next((chapter for chapter in chapters if chapter.id == current_chapter_id), None)
    chapter_number = _chapter_number(text)
    if chapter_number is not None and 1 <= chapter_number <= len(chapters):
        return chapters[chapter_number - 1]
    if not text:
        return None
    for chapter in chapters:
        title = _compact(chapter.title)
        if text == title or text in title or title in text:
            return chapter
    fuzzy_match = _fuzzy_chapter_match(chapters, text)
    if fuzzy_match:
        return fuzzy_match
    return None


def _fuzzy_chapter_match(chapters: list[MemoirBookPlanChapter], text: str) -> MemoirBookPlanChapter | None:
    if len(text) < 3:
        return None
    scored: list[tuple[float, MemoirBookPlanChapter]] = []
    for chapter in chapters:
        title = _compact(chapter.title)
        if not title:
            continue
        score = SequenceMatcher(None, text, title).ratio()
        scored.append((score, chapter))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return None
    best_score, best_chapter = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.72 or (best_score >= 0.62 and best_score - second_score >= 0.16):
        return best_chapter
    return None


def _latest_draft_content(chapter: MemoirBookPlanChapter) -> str:
    draft = chapter.draft
    if not draft or not draft.versions:
        return ""
    return (draft.versions[-1].content or "").strip()


def _draft_char_count(chapter: MemoirBookPlanChapter) -> int:
    return len(re.sub(r"\s+", "", _latest_draft_content(chapter)))


def _average_draft_char_count(chapters: list[MemoirBookPlanChapter]) -> int:
    counts = [_draft_char_count(chapter) for chapter in chapters]
    non_empty_counts = [count for count in counts if count > 0]
    if not non_empty_counts:
        return 0
    return round(sum(non_empty_counts) / len(non_empty_counts))


def _chapter_number(text: str) -> int | None:
    match = CHAPTER_NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return CHINESE_NUMBERS.get(raw)


def _normalized_direction(value: Any) -> str | None:
    text = _compact(str(value or ""))
    if text in {"up", "上移", "往前", "前移", "提前"}:
        return "up"
    if text in {"down", "下移", "往后", "后移"}:
        return "down"
    return None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _clean_reply_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value.strip()).strip()
    if not text:
        return None
    if len(text) > 280:
        return f"{text[:277]}..."
    return text


def _clean_title(value: str) -> str:
    return value.strip(" ，。,.!！?？\"'“”‘’")[:80]


def _normalize_chapter_word_text(value: str) -> str:
    text = value.strip()
    text = CHAPTER_WORD_ASR_ORDINAL_RE.sub(lambda match: f"第{match.group(1)}章", text)
    text = CHAPTER_WORD_ASR_BARE_RE.sub(lambda match: f"{match.group(1)}章", text)
    return (
        text.replace("几张", "几章")
        .replace("多少张", "多少章")
    )


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())
