from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.routers.memoir.voice_gateway import (
    _authenticate_websocket,
    _reject_websocket,
    voice_gateway_capacity_manager,
)
from app.services.memoir.debug_speaker_transcribe import (
    DebugSpeakerProofreadContextLine,
    DebugSpeakerTranscribeSession,
    proofread_debug_speaker_transcript_text,
)
from app.services.memoir.streaming_asr import (
    create_debug_speaker_streaming_asr_client,
    create_volc_streaming_asr_client,
)
from app.services.memoir.voice_gateway_capacity import VoiceGatewayCapacityError


router = APIRouter()


class DebugSpeakerProofreadContextLineRequest(BaseModel):
    speaker_id: str | None = None
    text: str = Field(..., min_length=1, max_length=500)
    start_ms: int | None = None
    end_ms: int | None = None


class DebugSpeakerProofreadRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    context_lines: list[DebugSpeakerProofreadContextLineRequest] = Field(default_factory=list, max_length=12)
    ai_model: str | None = None


class DebugSpeakerProofreadResponse(BaseModel):
    text: str
    proofread_text: str
    changed: bool
    provider: str | None = None
    context_line_count: int


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
            live_transcriber_factory=lambda: create_volc_streaming_asr_client(
                current_user_id,
                f"{debug_session_id}-live",
            ),
        )
        await session.run()
    finally:
        await capacity_lease.release()


@router.post("/debug/speaker-transcribe/proofread", response_model=DebugSpeakerProofreadResponse)
async def proofread_mobile_memoir_debug_speaker_transcript(
    req: DebugSpeakerProofreadRequest,
    _current_user: User = Depends(get_current_user),
):
    context_lines = [
        DebugSpeakerProofreadContextLine(
            speaker_id=line.speaker_id,
            text=line.text,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
        )
        for line in req.context_lines
    ]
    cleaned = await proofread_debug_speaker_transcript_text(
        req.text,
        context_lines=context_lines,
        ai_model=req.ai_model,
    )
    return DebugSpeakerProofreadResponse(
        text=req.text,
        proofread_text=cleaned.content,
        changed=cleaned.content.strip() != req.text.strip(),
        provider=cleaned.provider,
        context_line_count=len(context_lines),
    )
