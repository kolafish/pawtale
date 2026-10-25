from fastapi import APIRouter, Depends, Response, WebSocket, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_share_responses import (
    ShareResponseCreate, ShareResponseItem, ShareResponseList, ShareResponseVoiceTicket,
)
from app.services.memoir import share_responses as service, share_response_voice as voice_service
from app.schemas.memoir_share_likes import MemoirShareLikeTarget
from app.routers.websocket_auth import authenticate_websocket, reject_websocket
from app.routers.dictation_gateway import run_dictation_gateway
from app.services.memoir.voice_authorization import audio_authorizer, has_current_voice_authorization_consent
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.audio_files import audio_file_path
from fastapi.responses import FileResponse
import aiofiles
from app.routers.memoir.voice import _wav_bytes


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(dependencies=[Depends(no_store)])


@router.post("/share-responses", response_model=ShareResponseItem)
async def create_response(req: ShareResponseCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.create_response(db, user, req)


@router.get("/share-responses/{response_id}", response_model=ShareResponseItem)
async def read_response(response_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.private_response(db, response_id, user.id)


@router.post("/share-responses/{response_id}/read", response_model=ShareResponseItem)
async def mark_read(response_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.mark_read(db, response_id, user.id)


@router.post("/share-responses/{response_id}/adopt", response_model=ShareResponseItem)
async def adopt_response(response_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.adopt_response(db, response_id, user.id)


@router.get("/projects/{project_id}/share-responses", response_model=ShareResponseList)
async def list_responses(project_id: str, before_id: str | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.list_responses(db, project_id, user.id, before_id)


@router.post("/share-responses/voice", response_model=ShareResponseVoiceTicket)
async def start_voice(target: MemoirShareLikeTarget, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    capture = await voice_service.new_capture(db, user.id, target)
    return {"capture_id": capture.id}


@router.websocket("/share-responses/voice/{capture_id}")
async def response_voice(websocket: WebSocket, capture_id: str, db: AsyncSession = Depends(get_db)):
    user = await authenticate_websocket(websocket, db)
    if user is None:
        await reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    if not await has_current_voice_authorization_consent(db, user.id):
        await reject_websocket(websocket, "voice_authorization_required")
        return
    try:
        await voice_service.valid_capture(db, capture_id, user.id)
    except HTTPException:
        await reject_websocket(websocket, "share_response_voice_unavailable")
        return
    persistence = voice_service.ResponseCapturePersistence(capture_id=capture_id, user_id=user.id)
    consent = audio_authorizer(db, user.id)

    async def authorized() -> bool:
        return await consent() and await persistence.authorized()

    await run_dictation_gateway(websocket, user_id=user.id, capacity_prefix="memoir-share-response",
        diagnostics_context={"gateway": "memoir_share_response", "capture_id": capture_id},
        text_normalizer=normalize_memoir_asr_text, authorize_audio=authorized,
        on_audio_frame=persistence.record_audio_frame, on_audio_end=persistence.finish_audio,
        on_asr_final=persistence.persist_final, on_session_stop=persistence.stop)


@router.get("/share-responses/{response_id}/audio")
async def response_audio(response_id: str, response: Response,
                         db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    audio, _ = await voice_service.capture_audio(db, response_id, user.id)
    response.headers["Cache-Control"] = "no-store"
    path = audio_file_path(audio)
    if not path.exists():
        raise HTTPException(status_code=404, detail="原音文件不存在")
    if audio.mime_type == "audio/pcm":
        async with aiofiles.open(path, "rb") as file:
            return Response(content=_wav_bytes(await file.read()), media_type="audio/wav",
                headers={"Cache-Control": "no-store"})
    return FileResponse(path, media_type=audio.mime_type,
        headers={"Cache-Control": "no-store"})
