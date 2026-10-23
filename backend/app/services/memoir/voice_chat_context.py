from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.services.memoir.voice_followup_ai import MAX_CONTEXT_MESSAGES


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
            .where(
                MemoirChatMessage.project_id == voice_session.project_id,
                MemoirChatMessage.voice_session_id != voice_session.id,
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


async def load_related_topic_context_messages(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    current_message_count: int,
) -> list[MemoirChatMessage]:
    """Load a small history window from the most recent session for the selected topic."""
    remaining = max(0, MAX_CONTEXT_MESSAGES - current_message_count)
    anchor_id = voice_session.anchor_framework_item_id
    if remaining == 0 or not anchor_id:
        return []
    related_session_id = await db.scalar(
        select(MemoirVoiceSession.id)
        .outerjoin(
            MemoirVoiceUtterance,
            MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id,
        )
        .where(
            MemoirVoiceSession.project_id == voice_session.project_id,
            MemoirVoiceSession.id != voice_session.id,
            or_(
                MemoirVoiceSession.anchor_framework_item_id == anchor_id,
                MemoirVoiceUtterance.framework_item_id == anchor_id,
            ),
        )
        .order_by(MemoirVoiceSession.updated_at.desc(), MemoirVoiceSession.id.desc())
        .limit(1)
    )
    if related_session_id is None:
        return []
    messages = (
        await db.scalars(
            select(MemoirChatMessage)
            .where(
                MemoirChatMessage.voice_session_id == related_session_id,
                MemoirChatMessage.content_status == MemoirChatContentStatus.FINAL.value,
                MemoirChatMessage.content_text != "",
            )
            .order_by(MemoirChatMessage.turn_index, MemoirChatMessage.created_at, MemoirChatMessage.id)
        )
    ).all()
    return list(messages[-remaining:])
