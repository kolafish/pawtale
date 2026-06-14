from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir import (
    MemoirChatMessageCreate,
    MemoirChatMessageResponse,
    MemoirVoiceSessionCreate,
    MemoirVoiceSessionDetailResponse,
    MemoirVoiceSessionStopRequest,
    MemoirVoiceUtteranceCreate,
    MemoirVoiceUtteranceResponse,
)
from app.services.memoir import projects as memoir_projects
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir.access import load_memoir_project


router = APIRouter()


async def _memoir_project_id(
    db: AsyncSession,
    user_id: str,
    project_id: str | None = None,
) -> str:
    if project_id:
        project = await load_memoir_project(db, project_id, user_id)
        return project.id
    project = await memoir_projects.get_or_create_mobile_memoir_project(db, user_id)
    return project.id


@router.post("/voice-sessions", response_model=MemoirVoiceSessionDetailResponse, status_code=201)
async def create_mobile_memoir_voice_session(
    req: MemoirVoiceSessionCreate,
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_voice_sessions.create_voice_session(db, project_id, current_user.id, req)


@router.get("/voice-sessions/{voice_session_id}", response_model=MemoirVoiceSessionDetailResponse)
async def get_mobile_memoir_voice_session(
    voice_session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_sessions.get_voice_session_detail(db, voice_session_id, current_user.id)


@router.post("/voice-sessions/{voice_session_id}/stop", response_model=MemoirVoiceSessionDetailResponse)
async def stop_mobile_memoir_voice_session(
    voice_session_id: str,
    req: MemoirVoiceSessionStopRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_sessions.stop_voice_session(db, voice_session_id, current_user.id, req)


@router.post(
    "/voice-sessions/{voice_session_id}/messages",
    response_model=MemoirChatMessageResponse,
    status_code=201,
)
async def append_mobile_memoir_voice_message(
    voice_session_id: str,
    req: MemoirChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_sessions.append_chat_message(db, voice_session_id, current_user.id, req)


@router.post(
    "/voice-sessions/{voice_session_id}/utterances",
    response_model=MemoirVoiceUtteranceResponse,
    status_code=201,
)
async def append_mobile_memoir_voice_utterance(
    voice_session_id: str,
    req: MemoirVoiceUtteranceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_sessions.append_utterance(db, voice_session_id, current_user.id, req)
