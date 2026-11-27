from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_errors import ApplicationError
from app.auth import is_admin_user
from app.database import get_db
from app.models.users import User
from app.routers.dictation_gateway import run_dictation_gateway
from app.routers.websocket_auth import authenticate_websocket, reject_websocket
from app.services.memoir.access import load_memoir_project
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.voice_authorization import audio_authorizer, has_current_voice_authorization_consent


router = APIRouter(tags=["memoir-dictation"])


@router.websocket("/projects/{project_id}/dictation")
async def stream_memoir_project_dictation(
    websocket: WebSocket,
    project_id: str,
    db: AsyncSession = Depends(get_db),
    chapter_edit: bool = False,
):
    current_user = await authenticate_websocket(websocket, db)
    if current_user is None:
        await reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    if not await has_current_voice_authorization_consent(db, current_user.id):
        await reject_websocket(websocket, "voice_authorization_required")
        return
    if not await _memoir_project_dictation_is_available(
        db,
        project_id=project_id,
        current_user=current_user,
    ):
        await reject_websocket(websocket, "memoir_project_dictation_unavailable")
        return

    def normalize(text: str) -> str:
        text = normalize_memoir_asr_text(text)
        return normalize_memoir_asr_text(text, chapter_edit=True) if chapter_edit else text

    await run_dictation_gateway(
        websocket,
        user_id=current_user.id,
        capacity_prefix="memoir-project-dictation",
        diagnostics_context={
            "gateway": "memoir_project_dictation",
            "project_id": project_id,
        },
        text_normalizer=normalize,
        authorize_audio=audio_authorizer(db, current_user.id),
    )


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
