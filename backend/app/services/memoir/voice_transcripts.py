from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir import (
    MemoirVoiceTranscriptCleanupRequest,
    MemoirVoiceTranscriptItemResponse,
    MemoirVoiceTranscriptListResponse,
    MemoirVoiceTranscriptUpdateRequest,
)
from app.services import get_ai_service
from app.services.ai_types import AITextClient


SYSTEM_PROMPT = "你是一位谨慎的中文口述稿校对助手，只做忠于原话的转写校正和口头语清理。"
AI_CLEANUP_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
logger = logging.getLogger(__name__)
_FILLER = r"(?:嗯+|啊+|呃+|额+|那个|这个|就是)"
_REPEATED_FILLERS_RE = re.compile(rf"(?:{_FILLER}[\s，,、]*){{2,}}")
_BOUNDARY_FILLER_RE = re.compile(rf"(^|[。！？!?；;，,、]\s*)(?:嗯+|啊+|呃+|额+|就是)[\s，,、]*")
_BOUNDARY_DEICTIC_RE = re.compile(
    rf"(^|[。！？!?；;，,、]\s*)(?:那个|这个)(?=(?:就是|就|是|那个|这个|嗯|啊|呃|额|[\s，,、]))[\s，,、]*"
)
_DUPLICATE_PUNCTUATION_RE = re.compile(r"([，,。！？!?；;])\1+")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CleanedTranscriptText:
    content: str
    provider: str | None
    created_by: MemoirCreatedBy


def _text_or_empty(value: str | None) -> str:
    return (value or "").strip()


def _current_text(utterance: MemoirVoiceUtterance) -> str:
    return _text_or_empty(utterance.transcript_edited) or _text_or_empty(utterance.transcript_raw)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_cleanup_prompt(source_text: str) -> str:
    return f"""请对下面这段实时语音识别出来的中文口述内容做一次忠于原文的清理。

要求：
- 只修正 ASR 可能因方言、口音、同音词造成的明显错字或错词；无法确定时保留原文
- 必须去掉明显的口头填充词，比如句首或重复出现的“嗯”“啊”“呃”“那个”“这个”“就是”
- 必须去掉同一句话里明显无信息增量的重复表达，例如“那个那个那个”“就是就是”
- 可以整理基础标点和极少量语序，使句子可读
- 不新增事实、人物、时间、地点、事件或情绪
- 不扩写、不总结、不文学化、不替用户补充细节
- 输出长度应接近原文；短句保持短
- 直接输出清理后的第一人称正文，不要标题、列表、解释或引号

原始口述内容：
{source_text}
"""


def _strip_llm_wrapping(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] in {"“", "「", '"', "'"} and cleaned[-1] in {"”", "」", '"', "'"}:
        return cleaned[1:-1].strip()
    return cleaned


def _remove_obvious_oral_fillers(text: str) -> str:
    cleaned = _strip_llm_wrapping(text)
    cleaned = _REPEATED_FILLERS_RE.sub("", cleaned)
    cleaned = _BOUNDARY_FILLER_RE.sub(r"\1", cleaned)
    cleaned = _BOUNDARY_DEICTIC_RE.sub(r"\1", cleaned)
    cleaned = _DUPLICATE_PUNCTUATION_RE.sub(r"\1", cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip(" ，,、")
    cleaned = cleaned.replace("，。", "。").replace(",。", "。")
    return cleaned.strip()


def _fallback_cleaned_transcript_text(source_text: str) -> str:
    return _remove_obvious_oral_fillers(source_text) or source_text.strip()


async def generate_cleaned_transcript_text(
    source_text: str,
    *,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    strict: bool = True,
) -> CleanedTranscriptText:
    source_text = source_text.strip()
    if not source_text:
        raise HTTPException(status_code=400, detail="转写内容为空，无法清理")

    client: AITextClient | None = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        content = await client._call_llm(
            SYSTEM_PROMPT,
            _build_cleanup_prompt(source_text),
            temperature=0.2,
            max_tokens=1200,
            timeout=90.0,
        )
    except HTTPException as exc:
        if strict and exc.status_code < 500:
            raise
        if strict:
            logger.exception(
                "AI voice transcript cleanup failed provider=%s model=%s",
                getattr(client, "provider_name", "unknown"),
                getattr(client, "model_name", ai_model or "default"),
            )
            raise HTTPException(status_code=503, detail=AI_CLEANUP_UNAVAILABLE_MESSAGE) from exc
        logger.warning("AI voice transcript cleanup unavailable; using local fallback", exc_info=True)
        return CleanedTranscriptText(
            content=_fallback_cleaned_transcript_text(source_text),
            provider=None,
            created_by=MemoirCreatedBy.SYSTEM,
        )
    except Exception as exc:
        if strict:
            logger.exception(
                "AI voice transcript cleanup failed provider=%s model=%s",
                getattr(client, "provider_name", "unknown"),
                getattr(client, "model_name", ai_model or "default"),
            )
            raise HTTPException(status_code=503, detail=AI_CLEANUP_UNAVAILABLE_MESSAGE) from exc
        logger.warning("AI voice transcript cleanup failed; using local fallback", exc_info=True)
        return CleanedTranscriptText(
            content=_fallback_cleaned_transcript_text(source_text),
            provider=None,
            created_by=MemoirCreatedBy.SYSTEM,
        )

    cleaned = _remove_obvious_oral_fillers(str(content))
    if cleaned == source_text:
        cleaned = _fallback_cleaned_transcript_text(source_text)
    if not cleaned:
        if strict:
            raise HTTPException(status_code=502, detail="AI 未返回可用清理结果")
        cleaned = source_text
    return CleanedTranscriptText(
        content=cleaned,
        provider=client.provider_name,
        created_by=MemoirCreatedBy.AI,
    )


def _transcript_filter(user_id: str, project_id: str | None = None):
    clauses = [
        Project.user_id == user_id,
        or_(
            MemoirVoiceUtterance.transcript_raw != "",
            MemoirVoiceUtterance.transcript_edited != "",
        ),
    ]
    if project_id:
        clauses.append(Project.id == project_id)
    return clauses


def _item_response(utterance: MemoirVoiceUtterance) -> MemoirVoiceTranscriptItemResponse:
    voice_session = utterance.voice_session
    chat_message = utterance.chat_message
    audio_file = utterance.audio_file
    question_message = _question_message_for_utterance(utterance)
    raw = utterance.transcript_raw or ""
    edited = utterance.transcript_edited or ""
    display_text = edited.strip() or raw.strip()
    return MemoirVoiceTranscriptItemResponse(
        id=utterance.id,
        voice_session_id=utterance.voice_session_id,
        project_id=voice_session.project_id,
        chat_message_id=utterance.chat_message_id,
        audio_file_id=utterance.audio_file_id,
        audio_mime_type=audio_file.mime_type if audio_file else None,
        audio_size_bytes=audio_file.size_bytes if audio_file else None,
        audio_duration_seconds=audio_file.duration_seconds if audio_file else None,
        transcript_version_id=chat_message.transcript_version_id if chat_message else None,
        question_message_id=question_message.id if question_message else None,
        question_text=question_message.content_text if question_message else "",
        question_audio_file_id=question_message.audio_file_id if question_message else None,
        transcript_raw=raw,
        transcript_edited=edited,
        display_text=display_text,
        raw_char_count=len(raw.strip()),
        edited_char_count=len(edited.strip()),
        display_char_count=len(display_text),
        start_ms=utterance.start_ms,
        end_ms=utterance.end_ms,
        provider=utterance.provider,
        session_status=voice_session.status,
        guidance_mode=voice_session.guidance_mode,
        current_guidance_module=voice_session.current_guidance_module,
        created_at=_utc_datetime(utterance.created_at),
        session_created_at=_utc_datetime(voice_session.created_at),
    )


def _question_message_for_utterance(utterance: MemoirVoiceUtterance) -> MemoirChatMessage | None:
    user_message = utterance.chat_message
    if not user_message:
        return None
    candidates = [
        message
        for message in utterance.voice_session.messages
        if (
            message.role == MemoirChatMessageRole.ASSISTANT.value
            and message.content_text.strip()
            and message.turn_index < user_message.turn_index
        )
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda message: (message.turn_index, message.created_at, message.id))[-1]


def _detail_options():
    return (
        selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.messages),
        selectinload(MemoirVoiceUtterance.chat_message),
        selectinload(MemoirVoiceUtterance.audio_file),
    )


async def _load_owned_utterance(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
) -> MemoirVoiceUtterance:
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceUtterance.id == utterance_id, Project.user_id == user_id)
        .options(*_detail_options())
    )
    utterance = result.scalar_one_or_none()
    if not utterance:
        raise HTTPException(status_code=404, detail="转写记录不存在")
    return utterance


async def list_voice_transcripts(
    db: AsyncSession,
    user_id: str,
    *,
    project_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> MemoirVoiceTranscriptListResponse:
    filters = _transcript_filter(user_id, project_id)
    total = await db.scalar(
        select(func.count(MemoirVoiceUtterance.id))
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
    )
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
        .options(*_detail_options())
        .order_by(MemoirVoiceUtterance.created_at.desc(), MemoirVoiceUtterance.id.desc())
        .offset(offset)
        .limit(limit)
    )
    items = [_item_response(utterance) for utterance in result.scalars().all()]
    return MemoirVoiceTranscriptListResponse(
        items=items,
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def _apply_edited_text(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    content: str,
    *,
    version_type: MemoirTranscriptVersionType,
    created_by: MemoirCreatedBy,
    provider: str | None,
) -> MemoirVoiceTranscriptItemResponse:
    version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        version_type=version_type.value,
        content=content,
        provider=provider,
        created_by=created_by.value,
    )
    db.add(version)
    await db.flush()
    utterance.transcript_edited = content
    if utterance.chat_message:
        utterance.chat_message.content_text = content
        utterance.chat_message.content_status = MemoirChatContentStatus.CORRECTED.value
        utterance.chat_message.transcript_version_id = version.id
    await db.commit()
    return _item_response(utterance)


async def update_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    req: MemoirVoiceTranscriptUpdateRequest,
) -> MemoirVoiceTranscriptItemResponse:
    content = req.transcript_edited.strip()
    if not content:
        raise HTTPException(status_code=400, detail="转写内容不能为空")
    utterance = await _load_owned_utterance(db, utterance_id, user_id)
    return await _apply_edited_text(
        db,
        utterance,
        content,
        version_type=MemoirTranscriptVersionType.USER_EDITED,
        created_by=MemoirCreatedBy.USER,
        provider=None,
    )


async def cleanup_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    req: MemoirVoiceTranscriptCleanupRequest,
    ai_client: AITextClient | None = None,
) -> MemoirVoiceTranscriptItemResponse:
    utterance = await _load_owned_utterance(db, utterance_id, user_id)
    source_text = _current_text(utterance)
    cleaned = await generate_cleaned_transcript_text(
        source_text,
        ai_model=req.ai_model,
        ai_client=ai_client,
        strict=True,
    )
    return await _apply_edited_text(
        db,
        utterance,
        cleaned.content,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
        created_by=cleaned.created_by,
        provider=cleaned.provider,
    )


async def _message_reference_counts(
    db: AsyncSession,
    message: MemoirChatMessage,
    utterance_id: str,
) -> tuple[int, int]:
    other_utterances = await db.scalar(
        select(func.count(MemoirVoiceUtterance.id)).where(
            MemoirVoiceUtterance.chat_message_id == message.id,
            MemoirVoiceUtterance.id != utterance_id,
        )
    )
    child_messages = await db.scalar(
        select(func.count(MemoirChatMessage.id)).where(
            MemoirChatMessage.parent_message_id == message.id,
        )
    )
    return other_utterances or 0, child_messages or 0


async def delete_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
) -> None:
    utterance = await _load_owned_utterance(db, utterance_id, user_id)
    message = utterance.chat_message
    delete_message = False
    clear_message = False

    if message and message.role == MemoirChatMessageRole.USER.value:
        other_utterances, child_messages = await _message_reference_counts(db, message, utterance.id)
        delete_message = other_utterances == 0 and child_messages == 0
        clear_message = other_utterances == 0 and child_messages > 0

    await db.delete(utterance)
    await db.flush()

    if message and delete_message:
        await db.delete(message)
    elif message and clear_message:
        message.content_text = ""
        message.audio_file_id = None
        message.transcript_version_id = None
        message.content_status = MemoirChatContentStatus.CORRECTED.value

    await db.commit()
