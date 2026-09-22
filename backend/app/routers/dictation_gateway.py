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
from app.services.asr.gateway import AudioAuthorizationHandler, VoiceGatewaySession
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
) -> None:
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
        )
        await session.run()
    finally:
        await capacity_lease.release()
