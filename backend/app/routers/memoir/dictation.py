from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_errors import ApplicationError
from app.auth import is_admin_user
from app.database import get_db
from app.models.users import User
from app.routers.websocket_auth import authenticate_websocket, reject_websocket
from app.services.asr.capacity import (
    VoiceGatewayCapacityError,
    voice_gateway_capacity_manager,
)
from app.services.asr.factory import create_realtime_volc_streaming_asr_client
from app.services.asr.gateway import VoiceGatewaySession
from app.services.memoir.access import load_memoir_project
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text


router = APIRouter(tags=["memoir-dictation"])


@router.websocket("/projects/{project_id}/dictation")
async def stream_memoir_project_dictation(
    websocket: WebSocket,
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    current_user = await authenticate_websocket(websocket, db)
    if current_user is None:
        await reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    if not await _memoir_project_dictation_is_available(
        db,
        project_id=project_id,
        current_user=current_user,
    ):
        await reject_websocket(websocket, "memoir_project_dictation_unavailable")
        return

    connection_id = str(uuid.uuid4())
    capacity_session_id = f"memoir-project-dictation:{connection_id}"
    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(
            current_user.id,
            capacity_session_id,
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
                current_user.id,
                capacity_session_id,
            ),
            text_normalizer=normalize_memoir_asr_text,
            on_stop=release_capacity_before_stopped,
            connection_id=connection_id,
            diagnostics_context={
                "gateway": "memoir_project_dictation",
                "project_id": project_id,
            },
        )
        await session.run()
    finally:
        await capacity_lease.release()


async def _memoir_project_dictation_is_available(
    db: AsyncSession,
    *,
    project_id: str,
    current_user: User,
) -> bool:
    try:
        await load_memoir_project(
            db,
            project_id,
            current_user.id,
            allow_admin=is_admin_user(current_user),
        )
    except (ApplicationError, HTTPException):
        return False
    return True
