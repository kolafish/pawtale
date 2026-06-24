import base64
import binascii
from pathlib import Path
import struct
from typing import Literal
import uuid

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_voice import (
    MemoirAssistantTTSRequest,
    MemoirAssistantTTSResponse,
    MemoirVoiceAuthorizationConsentRequest,
    MemoirVoiceAuthorizationConsentResponse,
    MemoirVoiceAuthorizationConsentStatusResponse,
    MemoirVoiceAcknowledgementRequest,
    MemoirVoiceFollowUpRequest,
    MemoirVoiceFollowUpResponse,
    MemoirVoiceNoAnswerPromptRequest,
    MemoirVoiceProgressResponse,
    MemoirVoiceSearchQueryFiltersResponse,
    MemoirVoiceSearchQueryParseRequest,
    MemoirVoiceSearchQueryParseResponse,
    MemoirVoiceSearchTranscriptionRequest,
    MemoirVoiceSearchTranscriptionResponse,
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
from app.services.memoir import voice_authorization as memoir_voice_authorization
from app.services.memoir import voice_followups as memoir_voice_followups
from app.services.memoir import voice_search as memoir_voice_search
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir import voice_tts as memoir_voice_tts
from app.services.memoir import voice_transcripts as memoir_voice_transcripts
from app.services.memoir.access import load_audio_file, load_memoir_project
from app.services.memoir.audio_files import audio_file_path
from app.services.memoir.streaming_asr import create_volc_streaming_asr_client


router = APIRouter()
VoiceTranscriptIncludedFilter = Literal["all", "included", "not_included"]
VoiceTranscriptGuidanceModeFilter = Literal["free_narration", "guided_interview"]

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
    effective_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_sessions.list_voice_sessions(
        db,
        current_user.id,
        project_id=project_id,
        effective_only=effective_only,
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


@router.post(
    "/voice-authorization-consent",
    response_model=MemoirVoiceAuthorizationConsentResponse,
    status_code=201,
)
async def record_mobile_memoir_voice_authorization_consent(
    req: MemoirVoiceAuthorizationConsentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_authorization.record_voice_authorization_consent(db, current_user.id, req)


@router.get(
    "/voice-authorization-consent",
    response_model=MemoirVoiceAuthorizationConsentStatusResponse,
)
async def get_mobile_memoir_voice_authorization_consent(
    version: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    consent = await memoir_voice_authorization.latest_voice_authorization_consent(
        db,
        current_user.id,
        version.strip(),
    )
    return MemoirVoiceAuthorizationConsentStatusResponse(
        has_current_consent=bool(consent and consent.agreed),
        consent=consent,
    )


@router.post("/voice-sessions/{voice_session_id}/follow-up", response_model=MemoirVoiceFollowUpResponse)
async def create_mobile_memoir_voice_follow_up(
    voice_session_id: str,
    req: MemoirVoiceFollowUpRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_followups.generate_voice_follow_up(db, voice_session_id, current_user.id, req)


@router.post("/voice-sessions/{voice_session_id}/acknowledgement", response_model=MemoirVoiceFollowUpResponse)
async def create_mobile_memoir_voice_acknowledgement(
    voice_session_id: str,
    req: MemoirVoiceAcknowledgementRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_followups.generate_voice_acknowledgement(db, voice_session_id, current_user.id, req)


@router.post("/voice-sessions/{voice_session_id}/no-answer-prompt", response_model=MemoirVoiceFollowUpResponse)
async def create_mobile_memoir_voice_no_answer_prompt(
    voice_session_id: str,
    req: MemoirVoiceNoAnswerPromptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_followups.create_no_answer_prompt(db, voice_session_id, current_user.id, req)


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


@router.post("/search-query/transcribe", response_model=MemoirVoiceSearchTranscriptionResponse)
async def transcribe_mobile_memoir_search_query(
    req: MemoirVoiceSearchTranscriptionRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        audio = base64.b64decode(req.audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="搜索语音格式无效") from exc
    try:
        transcript = await memoir_voice_search.transcribe_voice_search_audio(
            audio,
            asr_client_factory=lambda: create_volc_streaming_asr_client(
                current_user.id,
                f"voice-search-{uuid.uuid4().hex}",
            ),
        )
    except memoir_voice_search.VoiceSearchTranscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return MemoirVoiceSearchTranscriptionResponse(transcript=transcript)


@router.post("/search-query/parse", response_model=MemoirVoiceSearchQueryParseResponse)
async def parse_mobile_memoir_search_query(
    req: MemoirVoiceSearchQueryParseRequest,
    current_user: User = Depends(get_current_user),
):
    parsed = await memoir_voice_search.parse_voice_search_query(req.text, ai_model=req.ai_model)
    return MemoirVoiceSearchQueryParseResponse(
        original_text=parsed.original_text,
        query=parsed.query,
        keywords=parsed.keywords,
        filters=MemoirVoiceSearchQueryFiltersResponse(
            included=parsed.filters.included,
            guidance_mode=parsed.filters.guidance_mode,
            module=parsed.filters.module,
        ),
        display_text=parsed.display_text,
        used_ai=parsed.used_ai,
        provider=parsed.provider,
        model=parsed.model,
    )


@router.get("/voice-transcripts", response_model=MemoirVoiceTranscriptListResponse)
async def list_mobile_memoir_voice_transcripts(
    project_id: str | None = None,
    q: str | None = Query(None, max_length=120),
    included: VoiceTranscriptIncludedFilter = "all",
    guidance_mode: VoiceTranscriptGuidanceModeFilter | None = None,
    module: str | None = Query(None, min_length=1, max_length=80),
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
        search_query=q,
        included=included,
        guidance_mode=guidance_mode,
        current_guidance_module=module,
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
