from pathlib import Path
import struct

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir import (
    MemoirAssistantTTSRequest,
    MemoirAssistantTTSResponse,
    MemoirVoiceFollowUpRequest,
    MemoirVoiceFollowUpResponse,
    MemoirVoiceProgressResponse,
    MemoirVoiceTranscriptCleanupRequest,
    MemoirVoiceTranscriptItemResponse,
    MemoirVoiceTranscriptListResponse,
    MemoirVoiceTranscriptUpdateRequest,
    MemoirVoiceSessionCreate,
    MemoirVoiceSessionDetailResponse,
    MemoirVoiceSessionListResponse,
    MemoirVoiceSessionStopRequest,
)
from app.services.memoir import projects as memoir_projects
from app.services.memoir import voice_followups as memoir_voice_followups
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir import voice_tts as memoir_voice_tts
from app.services.memoir import voice_transcripts as memoir_voice_transcripts
from app.services.memoir.access import load_audio_file, load_memoir_project
from app.services.memoir.audio_files import audio_file_path


router = APIRouter()

_PCM_SAMPLE_RATE = 16000
_PCM_CHANNELS = 1
_PCM_BITS_PER_SAMPLE = 16


def _wav_bytes(pcm: bytes) -> bytes:
    byte_rate = _PCM_SAMPLE_RATE * _PCM_CHANNELS * _PCM_BITS_PER_SAMPLE // 8
    block_align = _PCM_CHANNELS * _PCM_BITS_PER_SAMPLE // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        _PCM_CHANNELS,
        _PCM_SAMPLE_RATE,
        byte_rate,
        block_align,
        _PCM_BITS_PER_SAMPLE,
        b"data",
        len(pcm),
    )
    return header + pcm


async def _memoir_project_id(
    db: AsyncSession,
    user_id: str,
    project_id: str | None = None,
) -> str:
    if project_id:
        project = await load_memoir_project(db, project_id, user_id)
        return project.id
    project = await memoir_projects.active_mobile_memoir_project(db, user_id)
    if project is None:
        raise HTTPException(status_code=400, detail="请先创建回忆录")
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


@router.get("/voice-sessions", response_model=MemoirVoiceSessionListResponse)
async def list_mobile_memoir_voice_sessions(
    project_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_sessions.list_voice_sessions(
        db,
        current_user.id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )


@router.get("/voice-progress", response_model=MemoirVoiceProgressResponse)
async def get_mobile_memoir_voice_progress(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_sessions.get_voice_progress(db, current_user.id, project_id=project_id)


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


@router.post("/voice-sessions/{voice_session_id}/follow-up", response_model=MemoirVoiceFollowUpResponse)
async def create_mobile_memoir_voice_follow_up(
    voice_session_id: str,
    req: MemoirVoiceFollowUpRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_followups.generate_voice_follow_up(db, voice_session_id, current_user.id, req)


@router.post(
    "/voice-sessions/{voice_session_id}/messages/{message_id}/tts",
    response_model=MemoirAssistantTTSResponse,
)
async def create_mobile_memoir_assistant_tts(
    voice_session_id: str,
    message_id: str,
    req: MemoirAssistantTTSRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_tts.synthesize_assistant_message(
        db,
        voice_session_id,
        message_id,
        current_user.id,
        req,
    )


@router.get("/audio-files/{audio_file_id}/playback")
async def play_mobile_memoir_audio_file(
    audio_file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    audio_file = await load_audio_file(db, audio_file_id, current_user.id)
    path = audio_file_path(audio_file)
    if not path.exists():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    if audio_file.mime_type == "audio/pcm":
        async with aiofiles.open(path, "rb") as file:
            pcm = await file.read()
        return Response(
            content=_wav_bytes(pcm),
            media_type="audio/wav",
            headers={"Content-Disposition": f'inline; filename="{audio_file.id}.wav"'},
        )
    return FileResponse(
        path,
        media_type=audio_file.mime_type,
        filename=f"{audio_file.id}{Path(audio_file.storage_path).suffix}",
    )


@router.get("/voice-transcripts", response_model=MemoirVoiceTranscriptListResponse)
async def list_mobile_memoir_voice_transcripts(
    project_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if project_id:
        await load_memoir_project(db, project_id, current_user.id)
    return await memoir_voice_transcripts.list_voice_transcripts(
        db,
        current_user.id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )


@router.patch("/voice-transcripts/{utterance_id}", response_model=MemoirVoiceTranscriptItemResponse)
async def update_mobile_memoir_voice_transcript(
    utterance_id: str,
    req: MemoirVoiceTranscriptUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_transcripts.update_voice_transcript(db, utterance_id, current_user.id, req)


@router.post("/voice-transcripts/{utterance_id}/cleanup", response_model=MemoirVoiceTranscriptItemResponse)
async def cleanup_mobile_memoir_voice_transcript(
    utterance_id: str,
    req: MemoirVoiceTranscriptCleanupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_transcripts.cleanup_voice_transcript(db, utterance_id, current_user.id, req)


@router.delete("/voice-transcripts/{utterance_id}", status_code=204)
async def delete_mobile_memoir_voice_transcript(
    utterance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await memoir_voice_transcripts.delete_voice_transcript(db, utterance_id, current_user.id)
