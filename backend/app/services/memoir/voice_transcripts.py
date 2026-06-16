from __future__ import annotations

import logging
import re

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
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


def _text_or_empty(value: str | None) -> str:
    return (value or "").strip()


def _current_text(utterance: MemoirVoiceUtterance) -> str:
    return _text_or_empty(utterance.transcript_edited) or _text_or_empty(utterance.transcript_raw)


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
        created_at=utterance.created_at,
        session_created_at=voice_session.created_at,
    )


def _detail_options():
    return (
        selectinload(MemoirVoiceUtterance.voice_session),
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
    if not source_text:
        raise HTTPException(status_code=400, detail="转写内容为空，无法清理")

    client = ai_client or get_ai_service(req.ai_model)
    try:
        content = await client._call_llm(
            SYSTEM_PROMPT,
            _build_cleanup_prompt(source_text),
            temperature=0.2,
            max_tokens=1200,
            timeout=90.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception(
            "AI voice transcript cleanup failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_CLEANUP_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception(
            "AI voice transcript cleanup failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_CLEANUP_UNAVAILABLE_MESSAGE) from exc

    cleaned = _remove_obvious_oral_fillers(str(content))
    if cleaned == source_text:
        cleaned = _remove_obvious_oral_fillers(source_text)
    if not cleaned:
        raise HTTPException(status_code=502, detail="AI 未返回可用清理结果")
    return await _apply_edited_text(
        db,
        utterance,
        cleaned,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
        created_by=MemoirCreatedBy.AI,
        provider=client.provider_name,
    )
