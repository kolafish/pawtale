from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

from app.application_errors import InsufficientAICreditError
from app.routers.websocket_auth import reject_websocket
from app.services.asr.capacity import (
    VoiceGatewayCapacityError,
    voice_gateway_capacity_manager,
)
from app.services.asr.factory import create_realtime_volc_streaming_asr_client
from app.services.asr.gateway import (
    AudioAuthorizationHandler, AudioFrameHandler, AudioEndHandler, ASRFinalHandler,
    SessionStopHandler, VoiceGatewaySession,
)
from app.services.asr.billing import record_volc_streaming_asr_usage
from app.services.ai_credits import assert_ai_credit_available


def _identity_text(text: str) -> str:
    return text


async def run_dictation_gateway(
    websocket: WebSocket,
    *,
    user_id: str,
    capacity_prefix: str,
    diagnostics_context: dict[str, Any],
    text_normalizer: Callable[[str], str] = _identity_text,
    authorize_audio: AudioAuthorizationHandler | None = None,
    on_audio_frame: AudioFrameHandler | None = None,
    on_audio_end: AudioEndHandler | None = None,
    on_asr_final: ASRFinalHandler | None = None,
    on_session_stop: SessionStopHandler | None = None,
) -> None:
    session: VoiceGatewaySession | None = None
    stopped = False
    try:
        await assert_ai_credit_available(user_id)
    except InsufficientAICreditError:
        await reject_websocket(websocket, "ai_credit_insufficient")
        return
    connection_id = str(uuid.uuid4())
    capacity_session_id = f"{capacity_prefix}:{connection_id}"
    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(
            user_id, capacity_session_id
        )
    except VoiceGatewayCapacityError as exc:
        await reject_websocket(websocket, exc.code)
        return

    try:
        await websocket.accept()

        async def release_capacity_before_stopped(
            _error_message: str | None,
            _stop_reason: str,
        ) -> None:
            await capacity_lease.release()
            nonlocal stopped
            stopped = True
            if on_session_stop:
                await on_session_stop(_error_message, _stop_reason)

        extra_handlers = {}
        if on_audio_frame:
            extra_handlers["on_audio_frame"] = on_audio_frame
        if on_audio_end:
            extra_handlers["on_audio_end"] = on_audio_end
        if on_asr_final:
            extra_handlers["on_asr_final"] = on_asr_final
        session = VoiceGatewaySession(
            client=websocket,
            asr_client_factory=lambda: create_realtime_volc_streaming_asr_client(
                user_id, capacity_session_id
            ),
            on_stop=release_capacity_before_stopped,
            text_normalizer=text_normalizer,
            authorize_audio=authorize_audio,
            connection_id=connection_id,
            diagnostics_context=diagnostics_context,
            **extra_handlers,
        )
        await session.run()
    finally:
        try:
            if session is not None:
                audio_duration_seconds = float(getattr(session, "total_audio_duration_seconds", 0) or 0)
                terminal_error = getattr(session, "terminal_error", None)
                await record_volc_streaming_asr_usage(
                    audio_duration_seconds=audio_duration_seconds,
                    status="failed" if terminal_error else "succeeded",
                    error_type=terminal_error,
                    # The audio was streamed to the provider either way.
                    billable=True,
                )
        finally:
            try:
                if on_session_stop and not stopped:
                    await on_session_stop(getattr(session, "terminal_error", None), "transport_closed")
            finally:
                await capacity_lease.release()
