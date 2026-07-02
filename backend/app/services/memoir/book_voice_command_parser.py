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
from app.services.memoir.book_voice_command_context import (
    chapter_source_count as _chapter_source_count,
    draft_char_count as _draft_char_count,
    latest_draft_content as _latest_draft_content,
)
from app.services.memoir.book_voice_command_prompts import (
    BOOK_VOICE_COMMAND_SYSTEM_PROMPT,
    build_book_voice_command_user_prompt,
)
from app.services.memoir.book_voice_command_rules import (
    ANSWER_INTENTS,
    ASSIGN_SOURCE_RE,
    CAPABILITY_KEYWORDS,
    CHAPTER_NUMBER_RE,
    CHAPTER_SWAP_KEYWORDS,
    CONSULTATION_INTENTS,
    CURRENT_SOURCE_REFS,
    EXECUTABLE_MUTATION_INTENTS as _EXECUTABLE_MUTATION_INTENTS,
    INTENT_LABELS,
    MERGE_INTO_RE,
    MERGE_WITH_RE,
    MOVE_RE,
    MOVE_TO_INDEX_RE,
    MOVE_TO_POSITION_RE,
    NON_CONFIRMATION_INTENTS,
    OUTLINE_STATUS_KEYWORDS,
    RENAME_RE,
    SOURCE_ASSIGN_KEYWORDS,
    STRICT_CHAPTER_REF_RE,
    SUPPORTED_INTENTS,
    _chapter_number,
    _clean_chapter_ref,
    _clean_reply_text,
    _clean_source_ref,
    _clean_title,
    _compact,
    _fallback_chapter_ref,
    _fallback_consultation_intent,
    _is_ambiguous_follow_up,
    _is_chapter_count_question,
    _is_source_coverage_question,
    _is_source_read_request,
    _normalize_chapter_word_text,
    _normalized_direction,
    _normalized_relative_position,
    _normalized_target_position,
    _optional_int,
    _optional_text,
    _payload_has_move_target,
    _response_kind_for_intent,
    _source_read_ref,
    _source_ref_number,
)


logger = logging.getLogger(__name__)

EXECUTABLE_MUTATION_INTENTS = _EXECUTABLE_MUTATION_INTENTS

@dataclass(frozen=True)
class ParsedCommandPayload:
    intent: str
    chapter_ref: str | None = None
    target_chapter_ref: str | None = None
    source_ref: str | None = None
    new_title: str | None = None
    direction: str | None = None
    target_position: str | None = None
    target_index: int | None = None
    relative_chapter_ref: str | None = None
    relative_position: str | None = None
    summary: str | None = None
    reply_text: str | None = None
    used_ai: bool = False
    provider: str | None = None
    model: str | None = None


async def parse_payload_with_ai(
    req: MemoirBookVoiceCommandParseRequest,
    plan: MemoirBookPlan,
    *,
    source_count_by_chapter: dict[str, int] | None = None,
    ai_client: AITextClient | None = None,
) -> ParsedCommandPayload:
    fallback = _fallback_payload(
        req.text,
        plan=plan,
        conversation_context=req.conversation_context,
        book_context=req.book_context,
        source_count_by_chapter=source_count_by_chapter,
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
            build_book_voice_command_user_prompt(
                text=_normalize_chapter_word_text(req.text),
                plan=plan,
                current_chapter_id=req.current_chapter_id,
                conversation_context=req.conversation_context,
                book_context=req.book_context,
                source_count_by_chapter=source_count_by_chapter,
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
        if parsed.intent == "move_chapter" and not _payload_has_move_target(parsed) and fallback.intent == "move_chapter":
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
            target_position=parsed.target_position,
            target_index=parsed.target_index,
            relative_chapter_ref=parsed.relative_chapter_ref,
            relative_position=parsed.relative_position,
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
        target_position=_normalized_target_position(payload.get("target_position")),
        target_index=_optional_int(payload.get("target_index")),
        relative_chapter_ref=_optional_text(payload.get("relative_chapter_ref")),
        relative_position=_normalized_relative_position(payload.get("relative_position")),
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
            _payload_has_move_target(payload)
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
    source_count_by_chapter: dict[str, int] | None = None,
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

    assign_payload = _fallback_assign_source_payload(normalized, book_context, conversation_context)
    if assign_payload:
        return assign_payload

    if _is_source_coverage_question(normalized):
        return ParsedCommandPayload(
            intent="answer_book_question",
            summary="回答当前素材收入章节状态。",
            reply_text=_source_coverage_reply_text(
                plan,
                book_context,
                source_count_by_chapter=source_count_by_chapter,
            ),
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

    position_move_payload = _fallback_position_move_payload(normalized)
    if position_move_payload:
        return position_move_payload

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


def _fallback_position_move_payload(normalized: str) -> ParsedCommandPayload | None:
    index_match = MOVE_TO_INDEX_RE.search(normalized)
    if index_match:
        relative_position = _normalized_relative_position(index_match.group(3))
        target_index = _source_ref_number(index_match.group(2))
        if relative_position:
            return ParsedCommandPayload(
                intent="move_chapter",
                chapter_ref=_clean_chapter_ref(index_match.group(1)),
                relative_chapter_ref=f"第{index_match.group(2)}章",
                relative_position=relative_position,
            )
        return ParsedCommandPayload(
            intent="move_chapter",
            chapter_ref=_clean_chapter_ref(index_match.group(1)),
            target_index=target_index,
        )

    position_match = MOVE_TO_POSITION_RE.search(normalized)
    if not position_match:
        return None
    raw_position = position_match.group(2)
    target_position = "first" if raw_position in {"最前面", "最前", "开头", "第一章", "第一个章节"} else "last"
    return ParsedCommandPayload(
        intent="move_chapter",
        chapter_ref=_clean_chapter_ref(position_match.group(1)),
        target_position=target_position,
    )


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


def _source_list_position(
    book_context: MemoirBookVoiceCommandBookContext | None,
    source: MemoirBookVoiceCommandUncoveredSourceContext,
) -> int:
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    for index, item in enumerate(sources, start=1):
        if item.utterance_id == source.utterance_id:
            return index
    return 1


def _book_question_reply_text(
    original_text: str,
    plan: MemoirBookPlan,
    ai_reply_text: str | None,
    book_context: MemoirBookVoiceCommandBookContext | None,
    source_count_by_chapter: dict[str, int] | None = None,
) -> str:
    normalized = _compact(_normalize_chapter_word_text(original_text))
    cleaned_ai_reply = _clean_reply_text(ai_reply_text)
    if _is_source_read_request(normalized):
        return cleaned_ai_reply or _source_read_reply_text(book_context, None)
    if _is_source_coverage_question(normalized):
        return _source_coverage_reply_text(
            plan,
            book_context,
            source_count_by_chapter=source_count_by_chapter,
        )
    if cleaned_ai_reply:
        return cleaned_ai_reply
    if _is_chapter_count_question(normalized) or any(keyword in normalized for keyword in OUTLINE_STATUS_KEYWORDS):
        return _chapter_count_reply_text(plan)
    return _chapter_count_reply_text(plan)


def _source_coverage_reply_text(
    plan: MemoirBookPlan,
    book_context: MemoirBookVoiceCommandBookContext | None,
    *,
    source_count_by_chapter: dict[str, int] | None = None,
) -> str:
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
    covered_count = sum(_chapter_source_count(chapter, source_count_by_chapter) for chapter in chapters)
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
    source_count_by_chapter: dict[str, int] | None = None,
) -> MemoirBookVoiceCommandParseResponse:
    display_text = _normalize_chapter_word_text(original_text).strip()
    intent = payload.intent if payload.intent in SUPPORTED_INTENTS else "unknown"
    chapters = _sorted_chapters(plan)
    chapter = _resolve_chapter(chapters, payload.chapter_ref, current_chapter_id)
    target_chapter = _resolve_chapter(chapters, payload.target_chapter_ref, current_chapter_id)
    relative_chapter = _resolve_chapter(chapters, payload.relative_chapter_ref, current_chapter_id)
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
        target_position = payload.target_position if payload.target_position in {"first", "last"} else None
        target_index = payload.target_index if payload.target_index and 1 <= payload.target_index <= len(chapters) else None
        relative_position = payload.relative_position if payload.relative_position in {"before", "after"} else None
        if chapter and direction:
            operations.append(_operation(intent, chapter=chapter, direction=direction))
        elif chapter and target_position:
            operations.append(_operation(intent, chapter=chapter, target_position=target_position))
        elif chapter and target_index:
            operations.append(_operation(intent, chapter=chapter, target_index=target_index))
        elif chapter and relative_chapter and relative_position:
            operations.append(_operation(
                intent,
                chapter=chapter,
                relative_chapter=relative_chapter,
                relative_position=relative_position,
            ))
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
        reply_text = _consultation_reply_text(
            intent,
            chapter,
            chapters,
            payload.reply_text,
            source_count_by_chapter=source_count_by_chapter,
        )
    elif intent == "answer_book_question":
        summary = payload.summary or "回答当前成书章节状态。"
        reply_text = _book_question_reply_text(
            display_text,
            plan,
            payload.reply_text,
            book_context,
            source_count_by_chapter=source_count_by_chapter,
        )
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
    relative_chapter: MemoirBookPlanChapter | None = None,
    source: MemoirBookVoiceCommandUncoveredSourceContext | None = None,
    source_position: int | None = None,
    new_title: str | None = None,
    direction: str | None = None,
    target_position: str | None = None,
    target_index: int | None = None,
    relative_position: str | None = None,
) -> MemoirBookVoiceCommandOperationResponse:
    return MemoirBookVoiceCommandOperationResponse(
        type=operation_type,
        chapter_id=chapter.id if chapter else None,
        chapter_title=chapter.title if chapter else None,
        target_chapter_id=target_chapter.id if target_chapter else None,
        target_chapter_title=target_chapter.title if target_chapter else None,
        new_title=new_title,
        direction=direction,
        target_position=target_position,
        target_index=target_index,
        relative_chapter_id=relative_chapter.id if relative_chapter else None,
        relative_chapter_title=relative_chapter.title if relative_chapter else None,
        relative_position=relative_position,
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
        return _move_operation_summary(operation)
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


def _move_operation_summary(operation: MemoirBookVoiceCommandOperationResponse) -> str:
    if operation.direction:
        direction = "上移" if operation.direction == "up" else "下移"
        return f"准备把「{operation.chapter_title}」{direction}。"
    if operation.target_position == "first":
        return f"准备把「{operation.chapter_title}」移到第一章。"
    if operation.target_position == "last":
        return f"准备把「{operation.chapter_title}」移到最后一章。"
    if operation.target_index:
        return f"准备把「{operation.chapter_title}」移到第 {operation.target_index} 章。"
    if operation.relative_chapter_title and operation.relative_position:
        position = "前面" if operation.relative_position == "before" else "后面"
        return f"准备把「{operation.chapter_title}」移到「{operation.relative_chapter_title}」{position}。"
    return f"准备移动「{operation.chapter_title}」。"


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
    source_count_by_chapter: dict[str, int] | None = None,
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
            source_count = _chapter_source_count(chapter, source_count_by_chapter)
            return (
                f"「{chapter.title}」目前收录 {source_count} 条素材。"
                "结构上可以先按时间、地点或一个核心事件来收束；如果有两条主线，再考虑拆章。"
            )
        return (
            f"现在目录有 {len(chapters)} 章。"
            "整体结构可以先看每章是否只承担一个主题，再调整顺序或合并重复章节。"
        )

    return "我还没判断清楚。你可以直接问：第一章名字合适吗，或者第一章是不是太短了。"


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


def _average_draft_char_count(chapters: list[MemoirBookPlanChapter]) -> int:
    counts = [_draft_char_count(chapter) for chapter in chapters]
    non_empty_counts = [count for count in counts if count > 0]
    if not non_empty_counts:
        return 0
    return round(sum(non_empty_counts) / len(non_empty_counts))
