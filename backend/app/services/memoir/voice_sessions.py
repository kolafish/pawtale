from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirVoiceSessionStatus
from app.models.memoir import MemoirVoiceSession
from app.models.projects import Project
from app.schemas.memoir import (
    MemoirVoiceSessionCreate,
    MemoirVoiceSessionDetailResponse,
    MemoirVoiceSessionStopRequest,
)
from app.services.memoir.access import load_memoir_project, load_session


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


async def mark_voice_session_listening(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSessionDetailResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    if voice_session.status == MemoirVoiceSessionStatus.CREATED.value:
        voice_session.status = MemoirVoiceSessionStatus.LISTENING.value
        voice_session.error_message = ""
    await db.commit()
    return await get_voice_session_detail(db, voice_session.id, user_id)


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
