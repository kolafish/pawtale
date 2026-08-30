from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.models.projects import Project


async def load_voice_session(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSession:
    result = await db.execute(
        select(MemoirVoiceSession)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceSession.id == voice_session_id, Project.user_id == user_id)
        .options(selectinload(MemoirVoiceSession.messages))
    )
    voice_session = result.scalar_one_or_none()
    if not voice_session:
        raise HTTPException(status_code=404, detail="本次语音记录不存在")
    return voice_session


async def project_has_previous_narration(
    db: AsyncSession,
    voice_session: MemoirVoiceSession,
) -> bool:
    return bool(
        await db.scalar(
            select(1)
            .select_from(MemoirChatMessage)
            .join(
                MemoirVoiceSession,
                MemoirChatMessage.voice_session_id == MemoirVoiceSession.id,
            )
            .where(
                MemoirChatMessage.project_id == voice_session.project_id,
                MemoirChatMessage.voice_session_id != voice_session.id,
                MemoirVoiceSession.guidance_mode == voice_session.guidance_mode,
                MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
                MemoirChatMessage.content_text != "",
            )
            .limit(1)
        )
    )


async def next_turn_index(db: AsyncSession, voice_session_id: str) -> int:
    result = await db.execute(
        select(func.max(MemoirChatMessage.turn_index)).where(MemoirChatMessage.voice_session_id == voice_session_id)
    )
    current = result.scalar_one_or_none()
    return 0 if current is None else int(current) + 1
