from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirVoiceSessionStatus
from app.models.memoir import (
    MemoirAnswer,
    MemoirChatMessage,
    MemoirQuestion,
    MemoirSession,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir import (
    MemoirChatMessageCreate,
    MemoirVoiceSessionCreate,
    MemoirVoiceSessionDetailResponse,
    MemoirVoiceSessionStopRequest,
    MemoirVoiceUtteranceCreate,
)
from app.services.memoir.access import load_audio_file, load_memoir_project, load_session


def _detail_options():
    return (
        selectinload(MemoirVoiceSession.messages),
        selectinload(MemoirVoiceSession.utterances),
    )


async def _load_voice_session(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSession:
    result = await db.execute(
        select(MemoirVoiceSession)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceSession.id == voice_session_id, Project.user_id == user_id)
        .options(*_detail_options())
    )
    voice_session = result.scalar_one_or_none()
    if not voice_session:
        raise HTTPException(status_code=404, detail="语音会话不存在")
    return voice_session


async def create_voice_session(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: MemoirVoiceSessionCreate,
) -> MemoirVoiceSessionDetailResponse:
    project = await load_memoir_project(db, project_id, user_id)
    if req.memoir_session_id:
        memoir_session = await load_session(db, req.memoir_session_id, user_id)
        if memoir_session.project_id != project.id:
            raise HTTPException(status_code=404, detail="访谈不存在")

    now = datetime.utcnow()
    voice_session = MemoirVoiceSession(
        project_id=project.id,
        memoir_session_id=req.memoir_session_id,
        status=MemoirVoiceSessionStatus.CREATED.value,
        guidance_mode=req.guidance_mode.value,
        current_guidance_module=req.current_guidance_module,
        provider=req.provider,
        input_format=req.input_format,
        started_at=now,
        metadata_json=req.metadata_json,
    )
    db.add(voice_session)
    await db.commit()
    return await get_voice_session_detail(db, voice_session.id, user_id)


async def get_voice_session_detail(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSessionDetailResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    return MemoirVoiceSessionDetailResponse(
        session=voice_session,
        messages=list(voice_session.messages),
        utterances=list(voice_session.utterances),
    )


async def stop_voice_session(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceSessionStopRequest,
) -> MemoirVoiceSessionDetailResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    if voice_session.status not in {
        MemoirVoiceSessionStatus.STOPPED.value,
        MemoirVoiceSessionStatus.FAILED.value,
    }:
        voice_session.status = (
            MemoirVoiceSessionStatus.FAILED.value
            if req.error_message
            else MemoirVoiceSessionStatus.STOPPED.value
        )
        voice_session.error_message = req.error_message or ""
        voice_session.stopped_at = datetime.utcnow()
    await db.commit()
    return await get_voice_session_detail(db, voice_session.id, user_id)


async def append_chat_message(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirChatMessageCreate,
) -> MemoirChatMessage:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    await _assert_optional_audio_file(db, req.audio_file_id, user_id)
    await _assert_optional_transcript_version(db, req.transcript_version_id, voice_session.project_id)
    await _assert_optional_parent_message(db, req.parent_message_id, voice_session.id)

    next_turn_index = await _next_turn_index(db, voice_session.id)
    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=req.role.value,
        content_text=req.content_text,
        content_status=req.content_status.value,
        audio_file_id=req.audio_file_id,
        transcript_version_id=req.transcript_version_id,
        parent_message_id=req.parent_message_id,
        guidance_intent=req.guidance_intent,
        turn_index=next_turn_index,
        metadata_json=req.metadata_json,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def append_utterance(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceUtteranceCreate,
) -> MemoirVoiceUtterance:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    await _assert_optional_audio_file(db, req.audio_file_id, user_id)
    await _assert_optional_chat_message(db, req.chat_message_id, voice_session.id)
    if req.start_ms is not None and req.end_ms is not None and req.end_ms < req.start_ms:
        raise HTTPException(status_code=400, detail="音频结束时间不能早于开始时间")

    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        chat_message_id=req.chat_message_id,
        audio_file_id=req.audio_file_id,
        transcript_raw=req.transcript_raw,
        transcript_edited=req.transcript_edited if req.transcript_edited is not None else req.transcript_raw,
        start_ms=req.start_ms,
        end_ms=req.end_ms,
        provider=req.provider,
        provider_payload_json=req.provider_payload_json,
    )
    db.add(utterance)
    await db.commit()
    await db.refresh(utterance)
    return utterance


async def _next_turn_index(db: AsyncSession, voice_session_id: str) -> int:
    result = await db.execute(
        select(func.max(MemoirChatMessage.turn_index)).where(
            MemoirChatMessage.voice_session_id == voice_session_id
        )
    )
    current = result.scalar_one_or_none()
    return 0 if current is None else int(current) + 1


async def _assert_optional_audio_file(db: AsyncSession, audio_file_id: str | None, user_id: str) -> None:
    if audio_file_id:
        await load_audio_file(db, audio_file_id, user_id)


async def _assert_optional_transcript_version(
    db: AsyncSession,
    transcript_version_id: str | None,
    project_id: str,
) -> None:
    if not transcript_version_id:
        return
    result = await db.execute(
        select(MemoirTranscriptVersion)
        .join(MemoirAnswer, MemoirTranscriptVersion.answer_id == MemoirAnswer.id)
        .join(MemoirQuestion, MemoirAnswer.question_id == MemoirQuestion.id)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .where(MemoirTranscriptVersion.id == transcript_version_id)
        .where(MemoirSession.project_id == project_id)
    )
    transcript_version = result.scalar_one_or_none()
    if not transcript_version:
        raise HTTPException(status_code=404, detail="转写版本不存在")


async def _assert_optional_parent_message(
    db: AsyncSession,
    parent_message_id: str | None,
    voice_session_id: str,
) -> None:
    if not parent_message_id:
        return
    await _assert_optional_chat_message(db, parent_message_id, voice_session_id)


async def _assert_optional_chat_message(
    db: AsyncSession,
    chat_message_id: str | None,
    voice_session_id: str,
) -> None:
    if not chat_message_id:
        return
    result = await db.execute(
        select(MemoirChatMessage).where(
            MemoirChatMessage.id == chat_message_id,
            MemoirChatMessage.voice_session_id == voice_session_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="聊天消息不存在")
