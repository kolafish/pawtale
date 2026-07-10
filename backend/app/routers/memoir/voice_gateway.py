from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import SESSION_COOKIE_NAME, decode_session_token
from app.config import get_settings
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_voice import (
    MemoirVoiceSessionStopRequest,
)
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_voice_assistant_turn_stream import BookVoiceAssistantTurnStreamSession
from app.services.memoir.streaming_asr import create_realtime_volc_streaming_asr_client
from app.services.memoir.voice_assistant_turn_stream import VoiceAssistantTurnStreamSession
from app.services.memoir.voice_gateway import VoiceGatewaySession, VoiceGatewayStopReason
from app.services.memoir.voice_gateway_capacity import (
    VoiceGatewayCapacityError,
    VoiceGatewayCapacityLimits,
    VoiceGatewayCapacityManager,
)
from app.services.memoir.voice_gateway_persistence import VoiceGatewayPersistence


router = APIRouter()
logger = logging.getLogger(__name__)
voice_gateway_capacity_manager = VoiceGatewayCapacityManager(
    VoiceGatewayCapacityLimits.from_settings(get_settings())
)


@router.websocket("/voice-sessions/{voice_session_id}/gateway")
async def mobile_memoir_voice_gateway(
    websocket: WebSocket,
    voice_session_id: str,
    db: AsyncSession = Depends(get_db),
):
    current_user = await _authenticate_websocket(websocket, db)
    if current_user is None:
        await _reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    current_user_id = current_user.id

    try:
        await memoir_voice_sessions.get_voice_session_detail(db, voice_session_id, current_user_id)
    except HTTPException:
        await _reject_websocket(websocket, "voice_session_unavailable")
        return

    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(current_user_id, voice_session_id)
    except VoiceGatewayCapacityError as exc:
        await _reject_websocket(websocket, exc.code)
        return

    persistence: VoiceGatewayPersistence | None = None
    discard_pending_audio = True
    connection_id = str(uuid.uuid4())
    diagnostics_context = {
        "gateway": "memoir_voice_session",
        "voice_session_id": voice_session_id,
    }
    try:
        await websocket.accept()
        _log_voice_gateway_router_event(
            "websocket.accepted",
            connection_id=connection_id,
            **diagnostics_context,
        )
        persistence = VoiceGatewayPersistence(
            db=db,
            voice_session_id=voice_session_id,
            user_id=current_user_id,
        )

        async def on_start() -> None:
            await memoir_voice_sessions.mark_voice_session_listening(db, voice_session_id, current_user_id)

        async def on_stop(error_message: str | None, stop_reason: VoiceGatewayStopReason) -> None:
            nonlocal discard_pending_audio
            stop_error = error_message
            try:
                flushed = await persistence.flush_unfinalized_audio()
                if flushed:
                    _log_voice_gateway_router_event(
                        "unfinalized_audio.flushed",
                        connection_id=connection_id,
                        utterance_id=flushed.get("utterance_id") or "",
                        audio_file_id=flushed.get("audio_file_id") or "",
                        **diagnostics_context,
                    )
            except Exception as exc:
                stop_error = stop_error or str(exc)
                discard_pending_audio = False
                _log_voice_gateway_router_event(
                    "unfinalized_audio.flush_failed",
                    connection_id=connection_id,
                    level=logging.WARNING,
                    error=str(exc),
                    pending_audio_path=str(persistence.pending_audio_path or ""),
                    pending_audio_size_bytes=persistence.pending_audio_size_bytes,
                    **diagnostics_context,
                )
            if stop_reason != "transport_closed":
                await memoir_voice_sessions.stop_voice_session(
                    db,
                    voice_session_id,
                    current_user_id,
                    MemoirVoiceSessionStopRequest(error_message=stop_error),
                )

        gateway_session = VoiceGatewaySession(
            client=websocket,
            asr_client_factory=lambda: create_realtime_volc_streaming_asr_client(
                current_user_id,
                voice_session_id,
            ),
            on_start=on_start,
            on_turn_start=persistence.set_turn_context,
            on_stop=on_stop,
            on_audio_frame=persistence.record_audio_frame,
            on_asr_final=persistence.persist_final,
            connection_id=connection_id,
            diagnostics_context=diagnostics_context,
        )
        await gateway_session.run()
    finally:
        try:
            if persistence is not None and discard_pending_audio:
                await persistence.discard_pending_audio()
            elif persistence is not None and persistence.pending_audio_size_bytes:
                _log_voice_gateway_router_event(
                    "pending_audio.preserved",
                    connection_id=connection_id,
                    level=logging.WARNING,
                    pending_audio_path=str(persistence.pending_audio_path or ""),
                    pending_audio_size_bytes=persistence.pending_audio_size_bytes,
                    **diagnostics_context,
                )
        except Exception:
            logger.exception("Failed to discard pending memoir voice gateway audio")
        finally:
            await capacity_lease.release()


@router.websocket("/projects/{project_id}/book-voice-command/gateway")
async def mobile_memoir_book_voice_command_gateway(
    websocket: WebSocket,
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    current_user = await _authenticate_websocket(websocket, db)
    if current_user is None:
        await _reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    current_user_id = current_user.id

    try:
        await load_memoir_project(db, project_id, current_user_id)
    except HTTPException:
        await _reject_websocket(websocket, "memoir_project_unavailable")
        return

    transient_session_id = f"book-voice-command-{project_id}-{uuid.uuid4().hex}"
    capacity_user_id = f"{current_user_id}:book-voice-command"
    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(capacity_user_id, transient_session_id)
    except VoiceGatewayCapacityError as exc:
        await _reject_websocket(websocket, exc.code)
        return

    try:
        await websocket.accept()
        connection_id = str(uuid.uuid4())
        gateway_session = VoiceGatewaySession(
            client=websocket,
            asr_client_factory=lambda: create_realtime_volc_streaming_asr_client(
                current_user_id,
                transient_session_id,
            ),
            connection_id=connection_id,
            diagnostics_context={
                "gateway": "book_voice_command",
                "project_id": project_id,
                "voice_session_id": transient_session_id,
            },
        )
        await gateway_session.run()
    finally:
        await capacity_lease.release()


@router.websocket("/projects/{project_id}/book-voice-assistant/turn-stream")
async def mobile_memoir_book_voice_assistant_turn_stream(
    websocket: WebSocket,
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    current_user = await _authenticate_websocket(websocket, db)
    if current_user is None:
        await _reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    current_user_id = current_user.id

    try:
        await load_memoir_project(db, project_id, current_user_id)
    except HTTPException:
        await _reject_websocket(websocket, "memoir_project_unavailable")
        return

    capacity_user_id = f"{current_user_id}:book-voice-assistant-turn"
    capacity_session_id = f"book-voice-assistant-turn-{project_id}-{uuid.uuid4().hex}"
    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(capacity_user_id, capacity_session_id)
    except VoiceGatewayCapacityError as exc:
        await _reject_websocket(websocket, exc.code)
        return

    try:
        await websocket.accept()
        stream_session = BookVoiceAssistantTurnStreamSession(
            db=db,
            client=websocket,
            project_id=project_id,
            user_id=current_user_id,
        )
        await stream_session.run()
    finally:
        await capacity_lease.release()


@router.websocket("/voice-sessions/{voice_session_id}/assistant-turn-stream")
async def mobile_memoir_voice_assistant_turn_stream(
    websocket: WebSocket,
    voice_session_id: str,
    db: AsyncSession = Depends(get_db),
):
    current_user = await _authenticate_websocket(websocket, db)
    if current_user is None:
        await _reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    current_user_id = current_user.id

    try:
        await memoir_voice_sessions.get_voice_session_detail(db, voice_session_id, current_user_id)
    except HTTPException:
        await _reject_websocket(websocket, "voice_session_unavailable")
        return

    capacity_user_id = f"{current_user_id}:assistant-turn"
    capacity_session_id = f"{voice_session_id}:assistant-turn"
    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(capacity_user_id, capacity_session_id)
    except VoiceGatewayCapacityError as exc:
        await _reject_websocket(websocket, exc.code)
        return

    try:
        await websocket.accept()
        stream_session = VoiceAssistantTurnStreamSession(
            db=db,
            client=websocket,
            voice_session_id=voice_session_id,
            user_id=current_user_id,
        )
        await stream_session.run()
    finally:
        await capacity_lease.release()


async def _authenticate_websocket(websocket: WebSocket, db: AsyncSession) -> User | None:
    session_cookie = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not session_cookie:
        return None

    user_id = decode_session_token(session_cookie)
    if not user_id:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _reject_websocket(websocket: WebSocket, error: str) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "session.error",
            "state": "failed",
            "error": error,
        }
    )
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)


def _log_voice_gateway_router_event(event: str, *, level: int = logging.INFO, **payload: object) -> None:
    logger.log(
        level,
        "memoir_voice_gateway_router %s",
        json.dumps({"event": event, **payload}, ensure_ascii=False, sort_keys=True, default=str),
    )
