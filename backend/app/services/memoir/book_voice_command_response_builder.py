from __future__ import annotations

from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import (
    MemoirBookVoiceCommandBookContext,
    MemoirBookVoiceCommandOperationResponse,
    MemoirBookVoiceCommandParseResponse,
    MemoirBookVoiceCommandUncoveredSourceContext,
)
from app.services.memoir.book_plan_sources import (
    find_chapter_by_id as _chapter_by_id,
    sorted_chapters as _sorted_chapters,
)
from app.services.memoir.book_voice_command_context import (
    chapter_source_count as _chapter_source_count,
    draft_char_count as _draft_char_count,
    latest_draft_content as _latest_draft_content,
)
from app.services.memoir.book_voice_command_payloads import ParsedCommandPayload
from app.services.memoir.book_voice_command_resolution import (
    latest_source_ref_from_context,
    resolve_chapter,
    resolve_uncovered_source,
    source_list_position,
)
from app.services.memoir.book_voice_command_rules import (
    ANSWER_INTENTS,
    CONSULTATION_INTENTS,
    INTENT_LABELS,
    NON_CONFIRMATION_INTENTS,
    OUTLINE_STATUS_KEYWORDS,
    SUPPORTED_INTENTS,
    _clean_reply_text,
    _clean_title,
    _compact,
    _is_chapter_count_question,
    _is_source_coverage_question,
    _is_source_read_request,
    _normalize_chapter_word_text,
    _response_kind_for_intent,
    _source_ref_number,
)


def book_question_reply_text(
    original_text: str,
    plan: MemoirBookPlan,
    ai_reply_text: str | None,
    book_context: MemoirBookVoiceCommandBookContext | None,
    source_count_by_chapter: dict[str, int] | None = None,
) -> str:
    normalized = _compact(_normalize_chapter_word_text(original_text))
    cleaned_ai_reply = _clean_reply_text(ai_reply_text)
    if _is_source_read_request(normalized):
        return cleaned_ai_reply or source_read_reply_text(book_context, None)
    if _is_source_coverage_question(normalized):
        return source_coverage_reply_text(
            plan,
            book_context,
            source_count_by_chapter=source_count_by_chapter,
        )
    if cleaned_ai_reply:
        return cleaned_ai_reply
    if _is_chapter_count_question(normalized) or any(keyword in normalized for keyword in OUTLINE_STATUS_KEYWORDS):
        return chapter_count_reply_text(plan)
    return chapter_count_reply_text(plan)


def source_coverage_reply_text(
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


def source_read_reply_text(
    book_context: MemoirBookVoiceCommandBookContext | None,
    conversation_context: list[object] | None,
    source_ref: str | None = None,
) -> str:
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    if not sources:
        return "现在没有拿到可逐条处理的待收录记录。你可以先刷新成书页，或到下方待收录记录里查看。"
    last_position = 0
    latest_ref = latest_source_ref_from_context(conversation_context)
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


def chapter_count_reply_text(plan: MemoirBookPlan) -> str:
    chapters = _sorted_chapters(plan)
    if not chapters:
        return "现在还没有章节。你可以先生成目录，再用语音让我读目录、改章名或调整顺序。"
    chapter_names = "，".join(f"第 {index} 章「{chapter.title}」" for index, chapter in enumerate(chapters, start=1))
    return f"现在目录有 {len(chapters)} 章：{chapter_names}。你可以继续问某一章是否太短，或直接说想怎么改。"


def capability_reply_text(plan: MemoirBookPlan, ai_reply_text: str | None) -> str:
    cleaned_ai_reply = _clean_reply_text(ai_reply_text)
    if cleaned_ai_reply:
        return cleaned_ai_reply
    chapters = _sorted_chapters(plan)
    count_text = f"现在有 {len(chapters)} 章，" if chapters else ""
    return (
        f"{count_text}我可以帮你读目录或读某章、改章名、上移下移章节、合并章节，"
        "也可以判断某章名字是否合适、篇幅是不是太短。真正改动前我会先复述并等你确认。"
    )


def clarification_reply_text(conversation_context: list[object] | None, ai_reply_text: str | None) -> str:
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


def unknown_reply_text(chapters: list[MemoirBookPlanChapter]) -> str:
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
    chapter = resolve_chapter(chapters, payload.chapter_ref, current_chapter_id)
    target_chapter = resolve_chapter(chapters, payload.target_chapter_ref, current_chapter_id)
    relative_chapter = resolve_chapter(chapters, payload.relative_chapter_ref, current_chapter_id)
    source = resolve_uncovered_source(book_context, payload.source_ref, None)

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
            operations.append(
                _operation(
                    intent,
                    chapter=chapter,
                    relative_chapter=relative_chapter,
                    relative_position=relative_position,
                )
            )
        else:
            intent = "unknown"
    elif intent == "assign_source":
        assignment_chapter = target_chapter or chapter
        if assignment_chapter and source:
            operations.append(
                _operation(
                    intent,
                    chapter=assignment_chapter,
                    source=source,
                    source_position=source_list_position(book_context, source),
                )
            )
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
        reply_text = book_question_reply_text(
            display_text,
            plan,
            payload.reply_text,
            book_context,
            source_count_by_chapter=source_count_by_chapter,
        )
    elif intent == "read_uncovered_source":
        summary = payload.summary or "读出一条待收录讲述记录。"
        reply_text = payload.reply_text or source_read_reply_text(book_context, None, payload.source_ref)
    elif intent == "capability_help":
        summary = payload.summary or "说明语音编辑可以做的修改。"
        reply_text = capability_reply_text(plan, payload.reply_text)
    elif intent == "clarify_previous":
        summary = payload.summary or "需要确认上一句指的是哪一项修改。"
        reply_text = clarification_reply_text(None, payload.reply_text)
    elif intent == "unknown":
        summary = payload.summary or _summary_for_operation(intent, operations)
        reply_text = unknown_reply_text(chapters)
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


def _average_draft_char_count(chapters: list[MemoirBookPlanChapter]) -> int:
    counts = [_draft_char_count(chapter) for chapter in chapters]
    non_empty_counts = [count for count in counts if count > 0]
    if not non_empty_counts:
        return 0
    return round(sum(non_empty_counts) / len(non_empty_counts))
