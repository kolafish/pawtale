from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_speaker_transcription import (
    SpeakerProofreadRequest,
    SpeakerProofreadResponse,
)
from app.routers.memoir.voice_gateway import (
    _authenticate_websocket,
    _reject_websocket,
    voice_gateway_capacity_manager,
)
from app.services.memoir.speaker_transcript_proofreading import (
    SpeakerProofreadContextLine,
    proofread_speaker_transcript_text,
)
from app.services.memoir.speaker_transcription import SpeakerTranscriptionSession
from app.services.memoir.streaming_asr import (
    create_speaker_transcription_asr_client,
    create_volc_streaming_asr_client,
)
from app.services.memoir.voice_gateway_capacity import VoiceGatewayCapacityError


router = APIRouter()


@router.websocket("/speaker-transcription/gateway")
async def mobile_memoir_speaker_transcription_gateway(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    current_user = await _authenticate_websocket(websocket, db)
    if current_user is None:
        await _reject_websocket(websocket, "voice_gateway_auth_failed")
        return

    current_user_id = current_user.id
    transcription_session_id = f"speaker-transcription-{uuid.uuid4().hex}"
    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(current_user_id, transcription_session_id)
    except VoiceGatewayCapacityError as exc:
        await _reject_websocket(websocket, exc.code)
        return

    try:
        await websocket.accept()
        session = SpeakerTranscriptionSession(
            client=websocket,
            transcriber_factory=lambda: create_speaker_transcription_asr_client(
                current_user_id,
                transcription_session_id,
            ),
            live_transcriber_factory=lambda: create_volc_streaming_asr_client(
                current_user_id,
                f"{transcription_session_id}-live",
            ),
        )
        await session.run()
    finally:
        await capacity_lease.release()


@router.post("/speaker-transcription/proofread", response_model=SpeakerProofreadResponse)
async def proofread_mobile_memoir_speaker_transcript(
    req: SpeakerProofreadRequest,
    _current_user: User = Depends(get_current_user),
):
    context_lines = [
        SpeakerProofreadContextLine(
            speaker_id=line.speaker_id,
            text=line.text,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
        )
        for line in req.context_lines
    ]
    cleaned = await proofread_speaker_transcript_text(
        req.text,
        context_lines=context_lines,
        ai_model=req.ai_model,
    )
    return SpeakerProofreadResponse(
        text=req.text,
        proofread_text=cleaned.content,
        changed=cleaned.content.strip() != req.text.strip(),
        provider=cleaned.provider,
        context_line_count=len(context_lines),
    )
