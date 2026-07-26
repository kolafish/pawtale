from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.memoir import MemoirBookPlan, MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirVoiceTranscriptVoiceEditChangeResponse,
    MemoirVoiceTranscriptVoiceEditPreviewRequest,
    MemoirVoiceTranscriptVoiceEditPreviewResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import AITextTimeoutProfile, ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter, source_ref_utterance_id
from app.services.memoir.project_ai_preferences import select_project_ai_model


SYSTEM_PROMPT = "你是一位谨慎的中文口述记录修正助手，只能按用户明确指出的细节错误修正当前这一条讲述记录。"
VOICE_EDIT_UNAVAILABLE_MESSAGE = "AI 语音修正暂时不可用，请稍后重试。"
VOICE_EDIT_INVALID_RESPONSE_MESSAGE = "AI 未返回可用修正预览"
VOICE_EDIT_ACTIONS = {"revise_transcript", "clarify", "unsupported"}
VOICE_EDIT_SCOPES = {"term", "sentence", "passage"}
MAX_CHANGE_ITEMS = 12
MAX_CHANGE_TEXT_LENGTH = 800
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptVoiceEditContext:
    utterance: MemoirVoiceUtterance
    project: Project
    current_text: str
    included_chapter_titles: tuple[str, ...]


def _current_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()


async def _load_owned_context(
    db: AsyncSession,
    *,
    utterance_id: str,
    user_id: str,
) -> TranscriptVoiceEditContext:
    utterance = await db.scalar(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceUtterance.id == utterance_id, Project.user_id == user_id)
        .options(
            selectinload(MemoirVoiceUtterance.voice_session).selectinload(
                MemoirVoiceSession.project
            ),
            selectinload(MemoirVoiceUtterance.chat_message).selectinload(MemoirChatMessage.parent_message),
        )
    )
    if utterance is None:
        raise HTTPException(status_code=404, detail="讲述记录不存在")
    current_text = _current_text(utterance)
    if not current_text:
        raise HTTPException(status_code=400, detail="讲述记录内容为空，无法语音修正")
    included_chapter_titles = await _included_chapter_titles(
        db,
        project_id=utterance.voice_session.project_id,
        utterance_id=utterance.id,
    )
    return TranscriptVoiceEditContext(
        utterance=utterance,
        project=utterance.voice_session.project,
        current_text=current_text,
        included_chapter_titles=tuple(included_chapter_titles),
    )


async def _included_chapter_titles(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
) -> list[str]:
    plans = (
        await db.execute(
            select(MemoirBookPlan)
            .where(MemoirBookPlan.project_id == project_id)
            .options(selectinload(MemoirBookPlan.chapters))
        )
    ).scalars().all()
    titles: list[str] = []
    for plan in plans:
        refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
        for chapter in sorted(plan.chapters, key=lambda item: item.sort_order):
            refs = refs_by_chapter.get(chapter.id, [])
            if any(source_ref_utterance_id(ref) == utterance_id for ref in refs):
                titles.append(chapter.title)
    return titles


def _build_prompt(context: TranscriptVoiceEditContext, instruction_text: str) -> str:
    utterance = context.utterance
    session = utterance.voice_session
    current_text = context.current_text
    included_chapters = "、".join(f"「{title}」" for title in context.included_chapter_titles) or "未收入章节"
    question_text = ""
    if utterance.chat_message and utterance.chat_message.parent_message:
        question_text = utterance.chat_message.parent_message.content_text.strip()
    return f"""请根据用户的语音修正指令，只修正下面这一条讲述记录。

重要边界：
- 支持三类局部修改：term 表示名词或短词替换，sentence 表示指定句子修改，passage 表示指定片段改写
- “把外婆改成奶奶”这类指令应定位并替换明确的名词；“第二句改成……”应修改完整目标句子；“把讲退休后的那段改成……”应定位唯一相关片段后改写
- 只能改用户修正指令明确指出的事实、人物、地点、年份、名称、用词、句子、片段或识别错误
- 不要润色整段，不要扩写，不要总结，不要文学化
- 不要把章节标题、AI 问题或上下文补进正文
- 没听清、指令不完整、目标在正文中不存在、或无法唯一确定改哪一处时，action 必须为 "clarify"
- 指令明显不是修正当前记录时，action 必须为 "unsupported"
- revise_transcript 的 changes 必须完整列出全部改动，按原文出现顺序排列
- 每个 change.before 必须是当前整理文字中可精确定位的连续原文，不能概括、改写或留空；change.after 是替换后的完整文字，可以为空表示删除
- 后端会根据 changes 重建完整正文，不要返回 edited_text、reason 或 reply_text
- changes 之外的文字和标点必须逐字保持不变
- 如果同一 before 在原文出现多次且指令没有说明具体位置，不要猜测，action 必须为 "clarify"
- 直接返回 JSON，不要 Markdown，不要解释

返回 JSON 格式：
{{
  "action": "revise_transcript" | "clarify" | "unsupported",
  "changes": [
    {{"scope": "term" | "sentence" | "passage", "before": "当前正文中的精确原文", "after": "替换后的文字"}}
  ],
  "confidence": 0.0
}}

当前记录信息：
- 记录 ID：{utterance.id}
- 引导模式：{session.guidance_mode}
- 当前主题：{session.current_guidance_module or "未指定"}
- 所属章节：{included_chapters}
- AI 提问：{question_text or "无"}
- ASR 原文：{utterance.transcript_raw.strip() or "无"}
- 当前整理文字：{current_text}

用户语音修正指令：
{instruction_text.strip()}
"""


def _bounded_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _clean_text(value: object, *, limit: int = 1200) -> str:
    return str(value or "").strip().strip("\"“”").strip()[:limit].strip()


def _inferred_scope(before: str, value: object) -> str:
    scope = str(value or "").strip().lower()
    if scope in VOICE_EDIT_SCOPES:
        return scope
    if len(before) <= 12 and not any(mark in before for mark in "，。！？；：,.!?;:"):
        return "term"
    if len(before) <= 80 and any(mark in before for mark in "。！？.!?"):
        return "sentence"
    return "passage"


def _parse_changes(value: object) -> list[MemoirVoiceTranscriptVoiceEditChangeResponse]:
    if not isinstance(value, list):
        return []
    changes: list[MemoirVoiceTranscriptVoiceEditChangeResponse] = []
    for item in value[:MAX_CHANGE_ITEMS]:
        if not isinstance(item, dict):
            continue
        before = _clean_text(item.get("before"), limit=MAX_CHANGE_TEXT_LENGTH)
        after = _clean_text(item.get("after"), limit=MAX_CHANGE_TEXT_LENGTH)
        if not before and not after:
            continue
        changes.append(
            MemoirVoiceTranscriptVoiceEditChangeResponse(
                scope=_inferred_scope(before, item.get("scope")),
                before=before,
                after=after,
            )
        )
    return changes


def _normalize_action(value: object) -> str:
    action = str(value or "").strip()
    return action if action in VOICE_EDIT_ACTIONS else "clarify"


def _edited_text_from_changes(
    current_text: str,
    changes: list[MemoirVoiceTranscriptVoiceEditChangeResponse],
) -> str | None:
    if not changes:
        return None
    parts: list[str] = []
    cursor = 0
    for change in changes:
        if not change.before:
            return None
        index = current_text.find(change.before, cursor)
        if index < 0:
            return None
        parts.extend((current_text[cursor:index], change.after))
        cursor = index + len(change.before)
    parts.append(current_text[cursor:])
    return "".join(parts)


def _invalid_revision_response(
    *,
    context: TranscriptVoiceEditContext,
    provider: str | None,
    model: str | None,
) -> MemoirVoiceTranscriptVoiceEditPreviewResponse:
    return MemoirVoiceTranscriptVoiceEditPreviewResponse(
        utterance_id=context.utterance.id,
        action="clarify",
        original_text=context.utterance.transcript_raw.strip(),
        current_text=context.current_text,
        edited_text=context.current_text,
        changes=[],
        confidence=0.0,
        requires_confirmation=True,
        reply_text="我没能准确定位要改的原文，请说清原词、完整句子或具体片段。",
        provider=provider,
        model=model,
    )


def _response_from_payload(
    *,
    context: TranscriptVoiceEditContext,
    payload: dict,
    provider: str | None,
    model: str | None,
) -> MemoirVoiceTranscriptVoiceEditPreviewResponse:
    action = _normalize_action(payload.get("action"))
    current_text = context.current_text
    max_edited_text_length = max(len(current_text) * 2, len(current_text) + MAX_CHANGE_TEXT_LENGTH)
    changes = _parse_changes(payload.get("changes"))
    if action == "revise_transcript":
        edited_text = _edited_text_from_changes(current_text, changes)
        if edited_text is None or edited_text == current_text:
            return _invalid_revision_response(
                context=context,
                provider=provider,
                model=model,
            )
        if len(edited_text) > max_edited_text_length:
            raise HTTPException(status_code=502, detail="AI 修正结果过长，请重新说明要改哪一处")
    else:
        edited_text = current_text
        changes = []
    return MemoirVoiceTranscriptVoiceEditPreviewResponse(
        utterance_id=context.utterance.id,
        action=action,
        original_text=context.utterance.transcript_raw.strip(),
        current_text=current_text,
        edited_text=edited_text,
        changes=changes,
        confidence=_bounded_confidence(payload.get("confidence")),
        requires_confirmation=True,
        reply_text=_default_reply_text(action),
        provider=provider,
        model=model,
    )


def _default_reply_text(action: str) -> str:
    if action == "revise_transcript":
        return "我按你的说明生成了一版修正，请确认后保存。"
    if action == "unsupported":
        return "这句话不像是在修正当前记录。"
    return "我还没听清要改哪一处，请再具体说一下。"


async def preview_voice_transcript_edit(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    req: MemoirVoiceTranscriptVoiceEditPreviewRequest,
    ai_client: AITextClient | None = None,
) -> MemoirVoiceTranscriptVoiceEditPreviewResponse:
    context = await _load_owned_context(db, utterance_id=utterance_id, user_id=user_id)
    selected_ai_model = select_project_ai_model(context.project, req.ai_model)
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(selected_ai_model)
        raw = await client._call_llm(
            SYSTEM_PROMPT,
            _build_prompt(context, req.instruction_text),
            temperature=0.1,
            max_tokens=1600,
            timeout=ai_text_timeout_seconds(AITextTimeoutProfile.GENERATION),
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception(
            "AI transcript voice edit failed provider=%s model=%s",
            getattr(client, "provider_name", "unknown"),
            getattr(client, "model_name", selected_ai_model or "default"),
        )
        raise HTTPException(status_code=503, detail=VOICE_EDIT_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception(
            "AI transcript voice edit failed provider=%s model=%s",
            getattr(client, "provider_name", "unknown"),
            getattr(client, "model_name", selected_ai_model or "default"),
        )
        raise HTTPException(status_code=503, detail=VOICE_EDIT_UNAVAILABLE_MESSAGE) from exc

    payload = extract_json_payload(str(raw))
    if not isinstance(payload, dict):
        logger.warning(
            "AI transcript voice edit returned invalid JSON provider=%s model=%s response_length=%s",
            getattr(client, "provider_name", "unknown"),
            getattr(client, "model_name", selected_ai_model or "default"),
            len(str(raw)),
        )
        raise HTTPException(status_code=502, detail=VOICE_EDIT_INVALID_RESPONSE_MESSAGE)
    return _response_from_payload(
        context=context,
        payload=payload,
        provider=getattr(client, "provider_name", None),
        model=getattr(client, "model_name", selected_ai_model),
    )
