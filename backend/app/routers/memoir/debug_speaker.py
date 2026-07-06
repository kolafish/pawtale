from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.memoir.voice_gateway import (
    _authenticate_websocket,
    _reject_websocket,
    voice_gateway_capacity_manager,
)
from app.services.memoir.debug_speaker_transcribe import DebugSpeakerTranscribeSession
from app.services.memoir.streaming_asr import create_debug_speaker_streaming_asr_client
from app.services.memoir.voice_gateway_capacity import VoiceGatewayCapacityError


router = APIRouter()


@router.websocket("/debug/speaker-transcribe/gateway")
async def mobile_memoir_debug_speaker_transcribe_gateway(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    current_user = await _authenticate_websocket(websocket, db)
    if current_user is None:
        await _reject_websocket(websocket, "voice_gateway_auth_failed")
        return

    current_user_id = current_user.id
    debug_session_id = f"debug-speaker-{uuid.uuid4().hex}"
    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(current_user_id, debug_session_id)
    except VoiceGatewayCapacityError as exc:
        await _reject_websocket(websocket, exc.code)
        return

    try:
        await websocket.accept()
        session = DebugSpeakerTranscribeSession(
            client=websocket,
            transcriber_factory=lambda: create_debug_speaker_streaming_asr_client(
                current_user_id,
                debug_session_id,
            ),
        )
        await session.run()
    finally:
        await capacity_lease.release()
