from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application_errors import ResourceNotFoundError
from app.models.memoir import MemoirVoiceAuthorizationConsent, MemoirVoiceSession
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirVoiceAuthorizationConsentRequest,
)


VOICE_AUTHORIZATION_CONSENT_TYPE = "voice_authorization"
CURRENT_VOICE_AUTHORIZATION_VERSION = "2026-09-21"
AUDIO_AUTHORIZATION_REFRESH_SECONDS = 1.0


async def _assert_source_session_owner(db: AsyncSession, user_id: str, voice_session_id: str) -> None:
    result = await db.execute(
        select(MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceSession.id == voice_session_id, Project.user_id == user_id)
    )
    if result.scalar_one_or_none() is None:
        raise ResourceNotFoundError("本次语音记录不存在")


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


async def has_current_voice_authorization_consent(db: AsyncSession, user_id: str) -> bool:
    consent = await latest_voice_authorization_consent(
        db, user_id, CURRENT_VOICE_AUTHORIZATION_VERSION
    )
    return bool(consent and consent.agreed)


def audio_authorizer(db: AsyncSession, user_id: str) -> Callable[[], Awaitable[bool]]:
    last_checked_at = 0.0
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)

    async def authorized() -> bool:
        nonlocal last_checked_at
        now = time.monotonic()
        if now - last_checked_at < AUDIO_AUTHORIZATION_REFRESH_SECONDS:
            return True
        async with sessions() as authorization_db:
            consent = await has_current_voice_authorization_consent(authorization_db, user_id)
        if consent:
            last_checked_at = now
        return consent

    return authorized
