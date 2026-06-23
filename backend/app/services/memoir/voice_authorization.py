from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirVoiceAuthorizationConsent, MemoirVoiceSession
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirVoiceAuthorizationConsentRequest,
)


VOICE_AUTHORIZATION_CONSENT_TYPE = "voice_authorization"


async def _assert_source_session_owner(db: AsyncSession, user_id: str, voice_session_id: str) -> None:
    result = await db.execute(
        select(MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceSession.id == voice_session_id, Project.user_id == user_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")


async def record_voice_authorization_consent(
    db: AsyncSession,
    user_id: str,
    req: MemoirVoiceAuthorizationConsentRequest,
) -> MemoirVoiceAuthorizationConsent:
    if req.source_session_id:
        await _assert_source_session_owner(db, user_id, req.source_session_id)
    consent = MemoirVoiceAuthorizationConsent(
        user_id=user_id,
        consent_type=VOICE_AUTHORIZATION_CONSENT_TYPE,
        version=req.version,
        agreed=req.agreed,
        source=req.source,
        source_session_id=req.source_session_id,
        metadata_json=req.metadata_json,
    )
    db.add(consent)
    await db.commit()
    await db.refresh(consent)
    return consent


async def latest_voice_authorization_consent(
    db: AsyncSession,
    user_id: str,
    version: str,
) -> MemoirVoiceAuthorizationConsent | None:
    result = await db.execute(
        select(MemoirVoiceAuthorizationConsent)
        .where(
            MemoirVoiceAuthorizationConsent.user_id == user_id,
            MemoirVoiceAuthorizationConsent.consent_type == VOICE_AUTHORIZATION_CONSENT_TYPE,
            MemoirVoiceAuthorizationConsent.version == version,
        )
        .order_by(
            MemoirVoiceAuthorizationConsent.created_at.desc(),
            MemoirVoiceAuthorizationConsent.id.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()
