from __future__ import annotations

from dataclasses import dataclass
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerKind,
)
from app.domain.memoir.types import MemoirBookPlanStatus, MemoirChatMessageRole, MemoirSemanticCacheStatus
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterCard,
    MemoirSourceCard,
    MemoirVoiceSession,
)
from app.schemas.memoir_book import (
    MemoirBookSourceRouteEvidence,
    MemoirBookSourceRouteResponse,
    MemoirFollowupAnswerInterpretationResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_plan_sources import (
    SourceSegment,
    source_ref_utterance_ids,
    source_segments,
    sorted_chapters,
)
from app.services.memoir.book_source_links import (
    covered_utterance_ids,
    plan_source_ref_dicts_by_chapter,
)
from app.services.memoir.chapter_cards import CHAPTER_CARD_ENGINE, build_chapter_card_context
from app.services.memoir.followup_answer_parts import validate_resolved_answer_parts
from app.services.memoir.source_cards import generate_or_reuse_source_card


SYSTEM_PROMPT = (
    "你是一位谨慎的中文回忆录素材整理编辑。"
    "你只根据给定上下文判断讲述的素材归属和补全回答用途。"
)
AI_SOURCE_ROUTER_UNAVAILABLE_MESSAGE = "AI 暂时无法判断讲述归属，请稍后重试。"
MAX_SOURCE_TEXT_CHARS = 1800
MAX_DRAFT_CONTEXT_CHARS = 500
MAX_QUOTE_CHARS = 120
MAX_RECENT_CONTEXT_MESSAGES = 8
ALLOWED_MATERIAL_TYPES = {"memoir_content", "ai_interaction", "meta_instruction", "noise", "too_short"}
ALLOWED_ACTIONS = {
    "assign_to_chapter",
    "record_as_context",
    "suggest_new_chapter",
    "hold_for_review",
    "ignore",
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceRouteFollowupContext:
    task_id: str
    chapter_id: str | None
    target_slot: str
    question: str
    purpose: str
    expected_answer_use: str
    known_context_summary: str
    story_id: str | None
    story_title: str
    story_summary: str


def _latest_draft_text(chapter: MemoirBookPlanChapter) -> str:
    draft = chapter.draft
    if not draft or not draft.versions:
        return ""
    latest = max(draft.versions, key=lambda item: (item.version, item.created_at, item.id))
    return (latest.content or "").strip()


def _clean_quote(value: object) -> str:
    return str(value or "").strip()[:MAX_QUOTE_CHARS]


def _source_by_utterance(sources: list[SourceSegment]) -> dict[str, SourceSegment]:
    return {source.utterance_id: source for source in sources}


def _chapter_source_quotes(
    *,
    source_refs: list[dict[str, object]],
    sources_by_utterance: dict[str, SourceSegment],
) -> list[str]:
    quotes: list[str] = []
    for utterance_id in source_ref_utterance_ids(source_refs):
        source = sources_by_utterance.get(utterance_id)
        if source and source.text.strip():
            quotes.append(f"#{source.source_index}: {source.text.strip()[:180]}")
    return quotes


def _chapter_digest(
    chapter: MemoirBookPlanChapter,
    *,
    source_refs: list[dict[str, object]],
    sources_by_utterance: dict[str, SourceSegment],
    chapter_card: MemoirChapterCard | None = None,
) -> dict[str, object]:
    draft_text = _latest_draft_text(chapter)
    return {
        "chapter_id": chapter.id,
        "title": chapter.title,
        "chapter_card": _chapter_card_digest(chapter_card),
        "source_quotes": _chapter_source_quotes(
            source_refs=source_refs,
            sources_by_utterance=sources_by_utterance,
        ),
        "latest_draft_excerpt": draft_text[:MAX_DRAFT_CONTEXT_CHARS],
    }


def _string_items(value: object, *, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text[:120])
        if len(items) >= limit:
            break
    return items


def _source_card_digest(card: MemoirSourceCard | None) -> dict[str, object] | None:
    if not card or card.status != MemoirSemanticCacheStatus.READY.value or not isinstance(card.card_json, dict):
        return None
    key_facts: list[str] = []
    raw_key_facts = card.card_json.get("key_facts")
    if isinstance(raw_key_facts, list):
        for item in raw_key_facts:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            quote = str(item.get("evidence_quote") or "").strip()
            if text and quote:
                key_facts.append(f"{text}（证据：{quote[:80]}）")
            elif text:
                key_facts.append(text[:120])
            if len(key_facts) >= 5:
                break
    return {
        "source_card_id": card.id,
        "summary": str(card.card_json.get("summary") or "").strip()[:260],
        "people": _string_items(card.card_json.get("people")),
        "places": _string_items(card.card_json.get("places")),
        "time_hints": _string_items(card.card_json.get("time_hints")),
        "event_tags": _string_items(card.card_json.get("event_tags")),
        "key_facts": key_facts,
        "open_questions": _string_items(card.card_json.get("open_questions"), limit=4),
    }


def _chapter_card_digest(card: MemoirChapterCard | None) -> dict[str, object] | None:
    if not card or card.status != MemoirSemanticCacheStatus.READY.value or not isinstance(card.card_json, dict):
        return None
    gaps: list[str] = []
    raw_gaps = card.card_json.get("gaps")
    if isinstance(raw_gaps, list):
        for item in raw_gaps:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or "").strip()
            message = str(item.get("message") or "").strip()
            if message:
                gaps.append(f"{severity or 'medium'}: {message[:140]}")
            if len(gaps) >= 4:
                break
    return {
        "chapter_card_id": card.id,
        "summary": str(card.card_json.get("summary") or "").strip()[:300],
        "covered": card.card_json.get("covered") if isinstance(card.card_json.get("covered"), dict) else {},
        "gaps": gaps,
        "next_questions": _string_items(card.card_json.get("next_questions"), limit=4),
    }


async def _load_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions),
        )
    )


async def _conversation_context(db: AsyncSession, source: SourceSegment) -> tuple[str, str]:
    session = await db.scalar(
        select(MemoirVoiceSession)
        .where(MemoirVoiceSession.id == source.voice_session_id)
        .options(selectinload(MemoirVoiceSession.messages))
    )
    if not session:
        return "", ""
    messages = sorted(session.messages, key=lambda item: (item.turn_index, item.created_at, item.id))
    user_message = next((message for message in messages if message.id == source.message_id), None)
    question = ""
    if user_message:
        previous_questions = [
            message
            for message in messages
            if (
                message.role in {
                    MemoirChatMessageRole.ASSISTANT.value,
                    MemoirChatMessageRole.INTERVIEWER.value,
                }
                and message.content_text.strip()
                and message.turn_index < user_message.turn_index
            )
        ]
        if previous_questions:
            question = previous_questions[-1].content_text.strip()

    context_lines: list[str] = []
    if user_message:
        nearby = [
            message
            for message in messages
            if abs(message.turn_index - user_message.turn_index) <= 4 and message.content_text.strip()
        ][-MAX_RECENT_CONTEXT_MESSAGES:]
    else:
        nearby = [message for message in messages if message.content_text.strip()][-MAX_RECENT_CONTEXT_MESSAGES:]
    for message in nearby:
        if message.role == MemoirChatMessageRole.USER.value:
            speaker = "叙述者"
        elif message.role == MemoirChatMessageRole.INTERVIEWER.value:
            speaker = "采访者"
        else:
            speaker = "AI"
        context_lines.append(f"{speaker}：{message.content_text.strip()}")
    return question, "\n".join(context_lines)


async def _generate_or_reuse_route_source_card(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    ai_model: str | None,
) -> MemoirSourceCard | None:
    try:
        card = await generate_or_reuse_source_card(
            db,
            project_id=project_id,
            utterance_id=utterance_id,
            ai_model=ai_model,
        )
    except Exception:
        logger.exception("memoir source routing source card context failed utterance_id=%s", utterance_id)
        return None
    return card if card.status == MemoirSemanticCacheStatus.READY.value else None


async def _ready_chapter_card_for_route(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterCard | None:
    try:
        context = await build_chapter_card_context(db, project_id=project_id, chapter_id=chapter_id)
    except Exception:
        logger.exception("memoir source routing chapter card context failed chapter_id=%s", chapter_id)
        return None
    return await db.scalar(
        select(MemoirChapterCard).where(
            MemoirChapterCard.project_id == project_id,
            MemoirChapterCard.chapter_id == chapter_id,
            MemoirChapterCard.engine == CHAPTER_CARD_ENGINE,
            MemoirChapterCard.input_signature == context.input_signature,
            MemoirChapterCard.status == MemoirSemanticCacheStatus.READY.value,
        )
    )


async def _ready_chapter_cards_for_route(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: list[str],
) -> dict[str, MemoirChapterCard]:
    cards: dict[str, MemoirChapterCard] = {}
    for chapter_id in chapter_ids:
        card = await _ready_chapter_card_for_route(db, project_id=project_id, chapter_id=chapter_id)
        if card:
            cards[chapter_id] = card
    return cards


def _build_route_prompt(
    *,
    source: SourceSegment,
    source_card: dict[str, object] | None,
    question_text: str,
    conversation_context: str,
    chapters: list[dict[str, object]],
    collection_chapter: MemoirBookPlanChapter | None = None,
    followup_context: SourceRouteFollowupContext | None = None,
) -> str:
    source_card_lines: list[str] = []
    if source_card:
        source_card_lines = [
            f"- source_card_id: {source_card.get('source_card_id')}",
            f"- summary: {source_card.get('summary') or '无'}",
            f"- people: {source_card.get('people') or []}",
            f"- places: {source_card.get('places') or []}",
            f"- time_hints: {source_card.get('time_hints') or []}",
            f"- event_tags: {source_card.get('event_tags') or []}",
            f"- key_facts: {source_card.get('key_facts') or []}",
            f"- open_questions: {source_card.get('open_questions') or []}",
        ]

    chapter_lines: list[str] = []
    for chapter in chapters:
        source_quotes = chapter.get("source_quotes")
        quote_lines = []
        if isinstance(source_quotes, list):
            quote_lines = [f"    - {str(item).strip()}" for item in source_quotes if str(item).strip()]
        chapter_card = chapter.get("chapter_card")
        chapter_card_lines = ["  chapter_card: 无"]
        if isinstance(chapter_card, dict) and chapter_card:
            chapter_card_lines = [
                f"  chapter_card_id: {chapter_card.get('chapter_card_id')}",
                f"  chapter_card_summary: {chapter_card.get('summary') or '无'}",
                f"  chapter_card_covered: {chapter_card.get('covered') or {}}",
                f"  chapter_card_gaps: {chapter_card.get('gaps') or []}",
                f"  chapter_card_next_questions: {chapter_card.get('next_questions') or []}",
            ]
        draft_excerpt = str(chapter.get("latest_draft_excerpt") or "").strip()
        chapter_lines.append(
            "\n".join(
                [
                    f"- chapter_id: {chapter['chapter_id']}",
                    f"  title: {chapter['title']}",
                    *chapter_card_lines,
                    "  existing_source_quotes:",
                    *(quote_lines or ["    - 无"]),
                    f"  latest_draft_excerpt: {draft_excerpt or '无'}",
                ]
            )
        )

    followup_output = ""
    followup_rules = ""
    followup_details = "无"
    if followup_context:
        followup_output = """,
  "followup_answer": {
    "status": "resolved | needs_confirmation",
    "answer_kind": "direct_narrative | clarification | fact_resolution | mixed | null",
    "direct_narrative_text": "可直接作为新增叙事事实处理的原文片段，没有则为空字符串",
    "clarification_text": "只用于解释已有内容含义或与章节关系的原文片段，没有则为空字符串",
    "fact_resolution_text": "纠正、确认或否定已有事实的原文片段，没有则为空字符串",
    "confirmation_question": "无法准确判断时，向用户提出的一句具体中文确认问题，否则为空字符串",
    "reason": "一句中文理由"
  }"""
        followup_rules = """
- 当前记录是对一个补全问题的回答，必须返回 followup_answer。
- 先判断当前讲述是否在回答“补全任务上下文”，再判断它能否直接作为正文；不要因为没有人物、时间、地点就先判成 noise。
- direct_narrative 表示回答提供了可以直接进入叙事事实层的新经历或新细节。
- clarification 表示回答主要解释已有文字、事件或素材与章节的关系；它是编辑上下文，不是可直接拼入正文的新段落。
- fact_resolution 表示回答纠正、确认或否定已有事实。
- 当 target_slot 以 semantic: 开头时，叙述者对章节开头、结尾、转场、人物关系或表达方式给出的明确选择，也是有效的 clarification。比如说明“这里应该用抽象场景开头”“这不是一件具体事件”“结尾应停在当时的感受”，都不能判成 noise 或 meta_instruction。
- 如果叙述者只是在抱怨系统、反问 AI、表示想不起来或要求换题，并没有给出内容或编辑判断，才按 ai_interaction、meta_instruction、noise 或 too_short 忽略，不要伪造成 clarification。
- mixed 只在同一回答确实同时包含上述至少两类内容时使用，并按用途拆开。
- 三个用途文本必须逐字、连续地来自当前讲述原文；不得改写、概括或补词。
- 能准确拆分时 status=resolved；无法准确判断用途或无法可靠拆分时 status=needs_confirmation、answer_kind=null，三个用途文本留空，并给出具体确认问题。
- needs_confirmation 时 action 必须是 hold_for_review，不得归章；不要猜测一个最可能用途。
- 纯 clarification 或纯 fact_resolution 仍属于 memoir_content，但不会作为新正文素材摄取。
"""
        followup_details = f"""task_id: {followup_context.task_id}
target_slot: {followup_context.target_slot or '无'}
question: {followup_context.question}
purpose: {followup_context.purpose}
expected_answer_use: {followup_context.expected_answer_use}
known_context_summary: {followup_context.known_context_summary or '无'}
target_story_id: {followup_context.story_id or '无'}
target_story_title: {followup_context.story_title or '无'}
target_story_summary: {followup_context.story_summary or '无'}"""

    return f"""请判断“当前讲述记录”是否应该进入回忆录章节，并给出严格 JSON。

输出 JSON 格式必须是：
{{
  "material_type": "memoir_content | ai_interaction | meta_instruction | noise | too_short",
  "action": "assign_to_chapter | suggest_new_chapter | hold_for_review | ignore",
  "target_chapter_id": "已有章节 id 或 null",
  "suggested_title": "建议新章节标题或 null",
  "confidence": 0.0,
  "reason": "一句中文理由",
  "evidence": [
    {{"quote": "原文证据", "reason": "为什么支持判断"}}
  ]{followup_output}
}}

判断标准：
- memoir_content：叙述者在讲具体人生经历、人物、地点、事情、时间、感受或影响。
- ai_interaction：叙述者在和 AI 对话、提问、纠正、催促、吐槽、选择话题或询问系统能力。
- meta_instruction：叙述者在要求换主题、整理章节、修改标题、操作页面等。
- noise：没有可成书的信息，或明显不是回忆录内容。
- too_short：内容太短，无法判断是否是回忆素材。

动作要求：
- 只有 material_type=memoir_content 时，才允许 assign_to_chapter、suggest_new_chapter 或 hold_for_review。
- ai_interaction、meta_instruction、noise、too_short 必须 action=ignore，不允许归章，不允许建议新章节。
- assign_to_chapter 只能选择下面已有章节 id；如果证据不足，不要硬归章。
- suggest_new_chapter 只在确实是新的回忆主题且不能归入现有章节时使用；必须给 suggested_title。
- 标题必须由这条讲述的真实回忆主题概括而来，不能截取问句、命令句、对 AI 的话；不能使用模板化“xxx的记忆”兜底。
- 如果无法可靠命名新章节，使用 hold_for_review 且 suggested_title=null。
- confidence 是你对 action 的把握，不是内容重要性。
- 如果下面给出了“定向采集章节”，说明这条讲述来自该章的补全访谈。用途判断明确后，正文素材才会按采集目的收入该章；无法判断时必须等待用户确认。你仍需准确判断 material_type，不要把“想不起来”、操作指令或与 AI 的对话误判为回忆素材。
{followup_rules}
- 只输出 JSON，不要 Markdown，不要解释。

定向采集章节：
{f"{collection_chapter.id} / {collection_chapter.title}" if collection_chapter else "无"}

补全任务上下文：
{followup_details}

当前讲述记录：
- source_index: {source.source_index}
- utterance_id: {source.utterance_id}
- text: {source.text.strip()[:MAX_SOURCE_TEXT_CHARS]}

当前讲述素材卡片：
{chr(10).join(source_card_lines) or "无"}

这条记录前最近的 AI 提问：
{question_text or "无"}

临近对话上下文：
{conversation_context or "无"}

已有章节：
{chr(10).join(chapter_lines) or "无"}
"""


def _route_payload_from_llm(content: object) -> dict[str, object]:
    payload = extract_json_payload(str(content))
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="AI 未返回可用归章 JSON")
    return payload


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _followup_answer_from_payload(
    payload: dict[str, object],
    *,
    source: SourceSegment,
    context: SourceRouteFollowupContext,
) -> MemoirFollowupAnswerInterpretationResponse:
    raw = payload.get("followup_answer")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=502, detail="AI 补全回答结果缺少用途判断")
    try:
        status = MemoirFollowupAnswerInterpretationStatus(
            str(raw.get("status") or "").strip()
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="AI 补全回答判断状态无效") from exc
    if status == MemoirFollowupAnswerInterpretationStatus.PENDING:
        raise HTTPException(status_code=502, detail="AI 不能返回未处理的补全回答状态")

    reason = str(raw.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=502, detail="AI 补全回答结果缺少理由")
    source_text = source.text.strip()
    direct_text = str(raw.get("direct_narrative_text") or "").strip()
    clarification_text = str(raw.get("clarification_text") or "").strip()
    fact_text = str(raw.get("fact_resolution_text") or "").strip()
    confirmation_question = str(raw.get("confirmation_question") or "").strip()

    if status == MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION:
        if raw.get("answer_kind") is not None or any((direct_text, clarification_text, fact_text)):
            raise HTTPException(status_code=502, detail="待确认的补全回答不能预设用途")
        if not confirmation_question:
            raise HTTPException(status_code=502, detail="待确认的补全回答缺少确认问题")
        return MemoirFollowupAnswerInterpretationResponse(
            task_id=context.task_id,
            status=status,
            confirmation_question=confirmation_question,
            reason=reason,
        )

    try:
        answer_kind = MemoirFollowupAnswerKind(str(raw.get("answer_kind") or "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="AI 补全回答用途无效") from exc
    try:
        validate_resolved_answer_parts(
            source_text=source_text,
            answer_kind=answer_kind,
            direct_narrative_text=direct_text,
            clarification_text=clarification_text,
            fact_resolution_text=fact_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"AI 补全回答拆分无效：{exc}") from exc
    if confirmation_question:
        raise HTTPException(status_code=502, detail="已判定的补全回答不能再返回确认问题")
    return MemoirFollowupAnswerInterpretationResponse(
        task_id=context.task_id,
        status=status,
        answer_kind=answer_kind,
        direct_narrative_text=direct_text,
        clarification_text=clarification_text,
        fact_resolution_text=fact_text,
        reason=reason,
    )


def _route_response_from_payload(
    payload: dict[str, object],
    *,
    source: SourceSegment,
    chapters_by_id: dict[str, MemoirBookPlanChapter],
    collection_chapter: MemoirBookPlanChapter | None = None,
    followup_context: SourceRouteFollowupContext | None = None,
) -> MemoirBookSourceRouteResponse:
    material_type = str(payload.get("material_type") or "").strip()
    action = str(payload.get("action") or "").strip()
    if material_type not in ALLOWED_MATERIAL_TYPES or action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=502, detail="AI 归章结果类型无效")
    if material_type != "memoir_content" and action != "ignore":
        raise HTTPException(status_code=502, detail="AI 将非回忆素材错误标记为可归章")
    if action == "record_as_context" and followup_context is None:
        raise HTTPException(status_code=502, detail="普通素材不能标记为补全上下文")

    followup_answer = (
        _followup_answer_from_payload(payload, source=source, context=followup_context)
        if followup_context and material_type == "memoir_content"
        else None
    )
    if followup_context and material_type == "memoir_content" and followup_answer is None:
        raise HTTPException(status_code=502, detail="补全回答缺少用途判断")

    target_chapter_id = _normalize_optional_text(payload.get("target_chapter_id"))
    target_chapter_title = None
    if (
        followup_answer is not None
        and followup_answer.status == MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION
    ):
        action = "hold_for_review"
        if collection_chapter is not None:
            target_chapter_id = collection_chapter.id
            target_chapter_title = collection_chapter.title
        elif target_chapter_id and target_chapter_id in chapters_by_id:
            target_chapter_title = chapters_by_id[target_chapter_id].title
    elif (
        followup_answer is not None
        and followup_answer.direct_narrative_text
        and collection_chapter is not None
    ):
        action = "assign_to_chapter"
        target_chapter_id = collection_chapter.id
        target_chapter_title = collection_chapter.title
    elif followup_answer is not None and (
        followup_answer.clarification_text or followup_answer.fact_resolution_text
    ):
        action = "record_as_context"
        if collection_chapter is not None:
            target_chapter_id = collection_chapter.id
            target_chapter_title = collection_chapter.title
    elif (
        collection_chapter is not None
        and followup_answer is not None
        and material_type == "memoir_content"
    ):
        action = "assign_to_chapter"
        target_chapter_id = collection_chapter.id
        target_chapter_title = collection_chapter.title
    elif action == "assign_to_chapter":
        if not target_chapter_id or target_chapter_id not in chapters_by_id:
            raise HTTPException(status_code=502, detail="AI 返回的目标章节无效")
        target_chapter_title = chapters_by_id[target_chapter_id].title
    elif target_chapter_id and target_chapter_id not in chapters_by_id:
        raise HTTPException(status_code=502, detail="AI 返回的候选章节无效")
    elif target_chapter_id:
        target_chapter_title = chapters_by_id[target_chapter_id].title

    suggested_title = _normalize_optional_text(payload.get("suggested_title"))
    if followup_answer is not None:
        suggested_title = None
    if action == "suggest_new_chapter" and not suggested_title:
        raise HTTPException(status_code=502, detail="AI 建议新章节但没有给出标题")
    if action != "suggest_new_chapter":
        suggested_title = None

    try:
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=502, detail="AI 归章置信度无效")
    if confidence < 0 or confidence > 1:
        raise HTTPException(status_code=502, detail="AI 归章置信度越界")

    evidence: list[MemoirBookSourceRouteEvidence] = []
    raw_evidence = payload.get("evidence")
    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            quote = _clean_quote(item.get("quote"))
            reason = str(item.get("reason") or "").strip()
            if quote:
                evidence.append(
                    MemoirBookSourceRouteEvidence(
                        utterance_id=source.utterance_id,
                        quote=quote,
                        reason=reason,
                    )
                )
    if not evidence:
        raise HTTPException(status_code=502, detail="AI 归章结果缺少证据")

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=502, detail="AI 归章结果缺少理由")
    if (
        collection_chapter is not None
        and followup_answer is not None
        and followup_answer.status == MemoirFollowupAnswerInterpretationStatus.RESOLVED
    ):
        confidence = max(confidence, 0.95)
        reason = f"该讲述来自“{collection_chapter.title}”的补全访谈；{reason}"

    return MemoirBookSourceRouteResponse(
        material_type=material_type,
        action=action,
        utterance_id=source.utterance_id,
        source_index=source.source_index,
        target_chapter_id=(
            target_chapter_id
            if action == "assign_to_chapter" or followup_answer is not None
            else None
        ),
        target_chapter_title=(
            target_chapter_title
            if action == "assign_to_chapter" or followup_answer is not None
            else None
        ),
        suggested_title=suggested_title,
        confidence=round(confidence, 3),
        reason=reason,
        assignment_basis=(
            "completion_task"
            if followup_answer is not None
            else "ai"
        ),
        followup_answer=followup_answer,
        evidence=evidence,
    )


async def route_book_source(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    utterance_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    followup_context: SourceRouteFollowupContext | None = None,
) -> MemoirBookSourceRouteResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    sources = await source_segments(db, user_id=user_id, project_id=project_id, include_ignored=True)
    source_by_utterance = _source_by_utterance(sources)
    source = source_by_utterance.get(utterance_id)
    if not source:
        raise HTTPException(status_code=404, detail="讲述记录不存在")
    if utterance_id in await covered_utterance_ids(db, plan):
        raise HTTPException(status_code=400, detail="讲述记录已收录到章节")

    chapters = sorted_chapters(plan)
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    chapters_by_id = {chapter.id: chapter for chapter in chapters}
    collection_chapter = (
        chapters_by_id.get(followup_context.chapter_id)
        if followup_context and followup_context.chapter_id
        else None
    )
    if followup_context and followup_context.chapter_id and collection_chapter is None:
        raise HTTPException(status_code=409, detail="补全访谈的目标章节已不存在")
    question_text, conversation_context = await _conversation_context(db, source)
    route_source_card = await _generate_or_reuse_route_source_card(
        db,
        project_id=project_id,
        utterance_id=utterance_id,
        ai_model=ai_model,
    )
    chapter_cards_by_id = await _ready_chapter_cards_for_route(
        db,
        project_id=project_id,
        chapter_ids=[chapter.id for chapter in chapters],
    )
    prompt = _build_route_prompt(
        source=source,
        source_card=_source_card_digest(route_source_card),
        question_text=question_text,
        conversation_context=conversation_context,
        chapters=[
            _chapter_digest(
                chapter,
                source_refs=refs_by_chapter.get(chapter.id, []),
                sources_by_utterance=source_by_utterance,
                chapter_card=chapter_cards_by_id.get(chapter.id),
            )
            for chapter in chapters
        ],
        collection_chapter=collection_chapter,
        followup_context=followup_context,
    )

    client = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        content = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.15,
            max_tokens=900,
            timeout=90.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir source routing failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_SOURCE_ROUTER_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir source routing failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_SOURCE_ROUTER_UNAVAILABLE_MESSAGE) from exc

    payload = _route_payload_from_llm(content)
    return _route_response_from_payload(
        payload,
        source=source,
        chapters_by_id=chapters_by_id,
        collection_chapter=collection_chapter,
        followup_context=followup_context,
    )
