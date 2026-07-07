from __future__ import annotations

import logging
import re
from typing import Any

from app.models.memoir import MemoirBookPlan
from app.schemas.memoir_book import (
    MemoirBookVoiceCommandBookContext,
    MemoirBookVoiceCommandParseRequest,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.book_plan_sources import (
    sorted_chapters as _sorted_chapters,
)
from app.services.memoir.book_voice_command_payloads import ParsedCommandPayload, parsed_payload_from_json
from app.services.memoir.book_voice_command_prompts import (
    BOOK_VOICE_COMMAND_SYSTEM_PROMPT,
    build_book_voice_command_user_prompt,
)
from app.services.memoir.book_voice_command_resolution import (
    latest_source_ref_from_context as _latest_source_ref_from_context,
    resolve_chapter as _resolve_chapter,
    resolve_uncovered_source as _resolve_uncovered_source,
    source_list_position as _source_list_position,
)
from app.services.memoir.book_voice_command_response_builder import (
    capability_reply_text as _capability_reply_text,
    chapter_count_reply_text as _chapter_count_reply_text,
    clarification_reply_text as _clarification_reply_text,
    source_coverage_reply_text as _source_coverage_reply_text,
    source_read_reply_text as _source_read_reply_text,
)
from app.services.memoir.book_voice_command_rules import (
    ASSIGN_SOURCE_RE,
    CAPABILITY_KEYWORDS,
    CHAPTER_NUMBER_RE,
    CHAPTER_SWAP_KEYWORDS,
    MERGE_INTO_RE,
    MERGE_WITH_RE,
    MOVE_RE,
    MOVE_TO_INDEX_RE,
    MOVE_TO_POSITION_RE,
    OUTLINE_STATUS_KEYWORDS,
    RENAME_RE,
    SOURCE_ASSIGN_KEYWORDS,
    STRICT_CHAPTER_REF_RE,
    _clean_chapter_ref,
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
    _payload_has_move_target,
    _source_read_ref,
    _source_ref_number,
)


logger = logging.getLogger(__name__)


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
        parsed = parsed_payload_from_json(payload)
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
