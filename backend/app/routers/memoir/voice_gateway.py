from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import SESSION_COOKIE_NAME, decode_session_token
from app.database import get_db
from app.models.users import User
from app.schemas.memoir import MemoirVoiceSessionStopRequest
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir.streaming_asr import create_volc_streaming_asr_client
from app.services.memoir.voice_gateway import VoiceGatewaySession


router = APIRouter()


@router.websocket("/voice-sessions/{voice_session_id}/gateway")
async def mobile_memoir_voice_gateway(
    websocket: WebSocket,
    voice_session_id: str,
    db: AsyncSession = Depends(get_db),
):
    current_user = await _authenticate_websocket(websocket, db)
    if current_user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        await memoir_voice_sessions.get_voice_session_detail(db, voice_session_id, current_user.id)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    async def on_stop(error_message: str | None) -> None:
        await memoir_voice_sessions.stop_voice_session(
            db,
            voice_session_id,
            current_user.id,
            MemoirVoiceSessionStopRequest(error_message=error_message),
        )

    gateway_session = VoiceGatewaySession(
        client=websocket,
        asr_client_factory=lambda: create_volc_streaming_asr_client(
            current_user.id,
            voice_session_id,
        ),
        on_stop=on_stop,
    )
    await gateway_session.run()


async def _authenticate_websocket(websocket: WebSocket, db: AsyncSession) -> User | None:
    session_cookie = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not session_cookie:
        return None

    user_id = decode_session_token(session_cookie)
    if not user_id:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
