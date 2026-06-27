from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirBookPlanStatus, MemoirChatMessageRole
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirVoiceSession,
)
from app.schemas.memoir_book import (
    MemoirBookSourceRouteEvidence,
    MemoirBookSourceRouteResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_plan_sources import (
    SourceSegment,
    covered_utterance_ids,
    source_ref_utterance_ids,
    source_segments,
    sorted_chapters,
)


SYSTEM_PROMPT = "你是一位谨慎的中文回忆录素材整理编辑。你只根据给定上下文判断一条讲述是否应该进入回忆录章节。"
AI_SOURCE_ROUTER_UNAVAILABLE_MESSAGE = "AI 暂时无法判断讲述归属，请稍后重试。"
MAX_SOURCE_TEXT_CHARS = 1800
MAX_DRAFT_CONTEXT_CHARS = 500
MAX_QUOTE_CHARS = 120
MAX_RECENT_CONTEXT_MESSAGES = 8
ALLOWED_MATERIAL_TYPES = {"memoir_content", "ai_interaction", "meta_instruction", "noise", "too_short"}
ALLOWED_ACTIONS = {"assign_to_chapter", "suggest_new_chapter", "hold_for_review", "ignore"}

logger = logging.getLogger(__name__)


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
    chapter: MemoirBookPlanChapter,
    *,
    sources_by_utterance: dict[str, SourceSegment],
) -> list[str]:
    quotes: list[str] = []
    for utterance_id in source_ref_utterance_ids(chapter.source_refs_json or []):
        source = sources_by_utterance.get(utterance_id)
        if source and source.text.strip():
            quotes.append(f"#{source.source_index}: {source.text.strip()[:180]}")
    return quotes


def _chapter_digest(
    chapter: MemoirBookPlanChapter,
    *,
    sources_by_utterance: dict[str, SourceSegment],
) -> dict[str, object]:
    draft_text = _latest_draft_text(chapter)
    return {
        "chapter_id": chapter.id,
        "title": chapter.title,
        "source_quotes": _chapter_source_quotes(chapter, sources_by_utterance=sources_by_utterance),
        "latest_draft_excerpt": draft_text[:MAX_DRAFT_CONTEXT_CHARS],
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
                message.role == MemoirChatMessageRole.ASSISTANT.value
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
        speaker = "叙述者" if message.role == MemoirChatMessageRole.USER.value else "AI"
        context_lines.append(f"{speaker}：{message.content_text.strip()}")
    return question, "\n".join(context_lines)


def _build_route_prompt(
    *,
    source: SourceSegment,
    question_text: str,
    conversation_context: str,
    chapters: list[dict[str, object]],
) -> str:
    chapter_lines: list[str] = []
    for chapter in chapters:
        source_quotes = chapter.get("source_quotes")
        quote_lines = []
        if isinstance(source_quotes, list):
            quote_lines = [f"    - {str(item).strip()}" for item in source_quotes if str(item).strip()]
        draft_excerpt = str(chapter.get("latest_draft_excerpt") or "").strip()
        chapter_lines.append(
            "\n".join(
                [
                    f"- chapter_id: {chapter['chapter_id']}",
                    f"  title: {chapter['title']}",
                    "  existing_source_quotes:",
                    *(quote_lines or ["    - 无"]),
                    f"  latest_draft_excerpt: {draft_excerpt or '无'}",
                ]
            )
        )

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
  ]
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
- 只输出 JSON，不要 Markdown，不要解释。

当前讲述记录：
- source_index: {source.source_index}
- utterance_id: {source.utterance_id}
- text: {source.text.strip()[:MAX_SOURCE_TEXT_CHARS]}

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


def _route_response_from_payload(
    payload: dict[str, object],
    *,
    source: SourceSegment,
    chapters_by_id: dict[str, MemoirBookPlanChapter],
) -> MemoirBookSourceRouteResponse:
    material_type = str(payload.get("material_type") or "").strip()
    action = str(payload.get("action") or "").strip()
    if material_type not in ALLOWED_MATERIAL_TYPES or action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=502, detail="AI 归章结果类型无效")
    if material_type != "memoir_content" and action != "ignore":
        raise HTTPException(status_code=502, detail="AI 将非回忆素材错误标记为可归章")

    target_chapter_id = _normalize_optional_text(payload.get("target_chapter_id"))
    target_chapter_title = None
    if action == "assign_to_chapter":
        if not target_chapter_id or target_chapter_id not in chapters_by_id:
            raise HTTPException(status_code=502, detail="AI 返回的目标章节无效")
        target_chapter_title = chapters_by_id[target_chapter_id].title
    elif target_chapter_id and target_chapter_id not in chapters_by_id:
        raise HTTPException(status_code=502, detail="AI 返回的候选章节无效")
    elif target_chapter_id:
        target_chapter_title = chapters_by_id[target_chapter_id].title

    suggested_title = _normalize_optional_text(payload.get("suggested_title"))
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

    return MemoirBookSourceRouteResponse(
        material_type=material_type,
        action=action,
        utterance_id=source.utterance_id,
        source_index=source.source_index,
        target_chapter_id=target_chapter_id if action == "assign_to_chapter" else None,
        target_chapter_title=target_chapter_title if action == "assign_to_chapter" else None,
        suggested_title=suggested_title,
        confidence=round(confidence, 3),
        reason=reason,
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
) -> MemoirBookSourceRouteResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    sources = await source_segments(db, user_id=user_id, project_id=project_id)
    source_by_utterance = _source_by_utterance(sources)
    source = source_by_utterance.get(utterance_id)
    if not source:
        raise HTTPException(status_code=404, detail="讲述记录不存在")
    if utterance_id in covered_utterance_ids(plan):
        raise HTTPException(status_code=400, detail="讲述记录已收录到章节")

    chapters = sorted_chapters(plan)
    chapters_by_id = {chapter.id: chapter for chapter in chapters}
    question_text, conversation_context = await _conversation_context(db, source)
    prompt = _build_route_prompt(
        source=source,
        question_text=question_text,
        conversation_context=conversation_context,
        chapters=[
            _chapter_digest(chapter, sources_by_utterance=source_by_utterance)
            for chapter in chapters
        ],
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
    return _route_response_from_payload(payload, source=source, chapters_by_id=chapters_by_id)
