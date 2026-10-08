import base64
import binascii
from pathlib import Path
import re
import struct
from typing import Literal
import uuid

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.services.ai_credits import assert_ai_credit_available
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_images import (
    MemoirMaterialImageGenerationJobResponse,
    MemoirMaterialImageGenerationRequest,
)
from app.schemas.memoir_voice import (
    MemoirSharedVideoReadRequest,
    MemoirSharedVideoResponse,
    MemoirAssistantTTSRequest,
    MemoirAssistantTTSResponse,
    MemoirMaterialOfflineASRRequest,
    MemoirMaterialOfflineASRStateResponse,
    MemoirMemoryActivationTraceDetailResponse,
    MemoirMemoryActivationTraceListResponse,
    MemoirAutomationEventAcknowledgeDismissibleResponse,
    MemoirAutomationEventListResponse,
    MemoirAutomationEventResponse,
    MemoirAutomationEventVoicePlaybackRequest,
    MemoirAutomationEventVoicePlaybackResponse,
    MemoirProjectProgressSnapshotResponse,
    MemoirVoiceAuthorizationConsentRequest,
    MemoirVoiceAuthorizationConsentResponse,
    MemoirVoiceAuthorizationConsentStatusResponse,
    MemoirVoiceFollowUpResponse,
    MemoirVoiceNoAnswerPromptRequest,
    MemoirVoiceProgressResponse,
    MemoirVoiceSearchQueryFiltersResponse,
    MemoirVoiceSearchQueryParseRequest,
    MemoirVoiceSearchQueryParseResponse,
    MemoirVoiceSearchTranscriptionRequest,
    MemoirVoiceSearchTranscriptionResponse,
    MemoirVoiceTranscriptCleanupRequest,
    MemoirVoiceTranscriptRecoveryRequest,
    MemoirVoiceTranscriptItemResponse,
    MemoirVoiceTranscriptListResponse,
    MemoirTranscriptShareVideoContextResponse,
    MemoirShareVideoCustomBackgroundAssetResponse,
    MemoirShareVideoJobCreateRequest,
    MemoirShareVideoDeleteRequest,
    MemoirShareVideoJobListResponse,
    MemoirShareVideoJobResponse,
    MemoirShareVideoCandidateCreateVideoRequest,
    MemoirShareVideoCandidateLatestResponse,
    MemoirShareVideoCandidateResponse,
    MemoirVoiceTranscriptUpdateRequest,
    MemoirVoiceTranscriptVoiceEditPreviewRequest,
    MemoirVoiceTranscriptVoiceEditPreviewResponse,
    MemoirVoiceSessionCreate,
    MemoirVoiceSessionDetailResponse,
    MemoirVoiceSessionListResponse,
    MemoirVoiceSessionStopRequest,
)
from app.services.memoir import projects as memoir_projects
from app.services.memoir import automation_events as memoir_automation_events
from app.services.memoir import progress_snapshot as memoir_progress_snapshot
from app.services.memoir import voice_authorization as memoir_voice_authorization
from app.services.memoir import voice_followups as memoir_voice_followups
from app.services.memoir import voice_search as memoir_voice_search
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir import voice_tts_persistence as memoir_voice_tts
from app.services.memoir import share_video_alignment as memoir_share_video_alignment
from app.services.memoir import share_video_custom_backgrounds as memoir_share_video_custom_backgrounds
from app.services.memoir import share_video_jobs as memoir_share_video_jobs
from app.services.memoir import share_video_shares as memoir_share_video_shares
from app.services.memoir import share_video_candidates as memoir_share_video_candidates
from app.services.memoir import voice_transcript_recovery as memoir_voice_transcript_recovery
from app.services.memoir import material_offline_asr_jobs as memoir_material_offline_asr_jobs
from app.services.memoir import material_image_generation as memoir_material_image_generation
from app.services.memoir import memory_activation as memoir_memory_activation
from app.services.memoir import transcript_voice_edit as memoir_transcript_voice_edit
from app.services.memoir import voice_transcripts as memoir_voice_transcripts
from app.services.memoir.access import load_audio_file, load_memoir_project
from app.services.memoir.audio_files import audio_file_path
from app.services.asr.factory import create_volc_streaming_asr_client


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


@router.get("/projects/{project_id}/progress-snapshot", response_model=MemoirProjectProgressSnapshotResponse)
async def get_mobile_memoir_project_progress_snapshot(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_progress_snapshot.get_project_progress_snapshot(
        db,
        user_id=current_user.id,
        project_id=project_id,
    )


@router.get(
    "/projects/{project_id}/memory-activation-traces",
    response_model=MemoirMemoryActivationTraceListResponse,
)
async def list_mobile_memoir_memory_activation_traces(
    project_id: str,
    voice_session_id: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_memory_activation.list_memory_activation_traces(
        db,
        project_id=project_id,
        user_id=current_user.id,
        allow_admin=is_admin_user(current_user),
        voice_session_id=voice_session_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/projects/{project_id}/memory-activation-traces/{trace_id}",
    response_model=MemoirMemoryActivationTraceDetailResponse,
)
async def get_mobile_memoir_memory_activation_trace(
    project_id: str,
    trace_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_memory_activation.get_memory_activation_trace(
        db,
        project_id=project_id,
        trace_id=trace_id,
        user_id=current_user.id,
        allow_admin=is_admin_user(current_user),
    )


@router.get("/projects/{project_id}/automation-events", response_model=MemoirAutomationEventListResponse)
async def list_mobile_memoir_automation_events(
    project_id: str,
    limit: int = Query(10, ge=1, le=50),
    completed_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_automation_events.list_automation_events(
        db,
        user_id=current_user.id,
        project_id=project_id,
        limit=limit,
        completed_only=completed_only,
    )


@router.post(
    "/projects/{project_id}/automation-events/acknowledge-dismissible",
    response_model=MemoirAutomationEventAcknowledgeDismissibleResponse,
)
async def acknowledge_dismissible_mobile_memoir_automation_events(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updated_count = await memoir_automation_events.acknowledge_dismissible_automation_events(
        db,
        user_id=current_user.id,
        project_id=project_id,
    )
    return MemoirAutomationEventAcknowledgeDismissibleResponse(updated_count=updated_count)


@router.post(
    "/projects/{project_id}/automation-events/{event_id}/acknowledge",
    response_model=MemoirAutomationEventResponse,
)
async def acknowledge_mobile_memoir_automation_event(
    project_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_automation_events.acknowledge_automation_event(
        db,
        user_id=current_user.id,
        project_id=project_id,
        event_id=event_id,
    )


@router.post(
    "/projects/{project_id}/automation-events/voice-played",
    response_model=MemoirAutomationEventVoicePlaybackResponse,
)
async def mark_mobile_memoir_automation_events_voice_played(
    project_id: str,
    req: MemoirAutomationEventVoicePlaybackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updated_count = await memoir_automation_events.mark_automation_events_voice_played(
        db,
        user_id=current_user.id,
        project_id=project_id,
        event_ids=req.event_ids,
        voice_session_id=req.voice_session_id,
        message_id=req.message_id,
    )
    return MemoirAutomationEventVoicePlaybackResponse(updated_count=updated_count)


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
    audio_file = await load_audio_file(
        db,
        audio_file_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        audio = base64.b64decode(req.audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="搜索语音格式无效") from exc
    if not await memoir_voice_authorization.has_current_voice_authorization_consent(db, current_user.id):
        raise HTTPException(status_code=403, detail="请先同意语音授权")
    await assert_ai_credit_available(current_user.id)
    try:
        transcript = await memoir_voice_search.transcribe_voice_search_audio(
            audio,
            asr_client_factory=lambda: create_volc_streaming_asr_client(
                current_user.id,
                f"voice-search-{uuid.uuid4().hex}",
            ),
            mime_type=req.mime_type,
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
    allow_admin = is_admin_user(current_user)
    if project_id:
        await load_memoir_project(db, project_id, current_user.id, allow_admin=allow_admin)
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
        allow_admin=allow_admin and bool(project_id),
    )


@router.get("/voice-transcripts/{utterance_id}", response_model=MemoirVoiceTranscriptItemResponse)
async def get_mobile_memoir_voice_transcript(
    utterance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_transcripts.get_voice_transcript(
        db,
        utterance_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/voice-transcripts/{utterance_id}/offline-asr",
    response_model=MemoirMaterialOfflineASRStateResponse,
    status_code=202,
)
async def enqueue_mobile_memoir_material_offline_asr(
    utterance_id: str,
    req: MemoirMaterialOfflineASRRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_material_offline_asr_jobs.enqueue_material_offline_asr(
        db,
        utterance_id=utterance_id,
        user_id=current_user.id,
        ai_model=req.ai_model,
        transcript_polish_level=req.transcript_polish_level,
        allow_admin=is_admin_user(current_user),
    )


@router.get(
    "/voice-transcripts/{utterance_id}/share-video/context",
    response_model=MemoirTranscriptShareVideoContextResponse,
)
async def get_mobile_memoir_voice_transcript_share_video_context(
    utterance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_share_video_alignment.get_share_video_context(
        db,
        utterance_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/voice-transcripts/{utterance_id}/image-generation/jobs",
    response_model=MemoirMaterialImageGenerationJobResponse,
    status_code=202,
)
async def create_mobile_memoir_material_image_generation_job(
    utterance_id: str,
    req: MemoirMaterialImageGenerationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_material_image_generation.create_material_image_generation_job(
        db,
        utterance_id=utterance_id,
        user_id=current_user.id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.get(
    "/image-generation/jobs/{job_id}",
    response_model=MemoirMaterialImageGenerationJobResponse,
)
async def get_mobile_memoir_material_image_generation_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_material_image_generation.get_material_image_generation_job(
        db,
        job_id=job_id,
        user_id=current_user.id,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/share-video/background-assets",
    response_model=MemoirShareVideoCustomBackgroundAssetResponse,
    status_code=201,
)
async def upload_mobile_memoir_share_video_background_asset(
    project_id: str = Form(...),
    processing_preset: str = Form("memoir_soft"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_share_video_custom_backgrounds.create_custom_share_video_background_asset(
        db,
        user_id=current_user.id,
        project_id=project_id,
        upload=file,
        processing_preset=processing_preset,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/voice-transcripts/{utterance_id}/share-video/jobs",
    response_model=MemoirShareVideoJobResponse,
    status_code=201,
)
async def create_mobile_memoir_voice_transcript_share_video_job(
    utterance_id: str,
    req: MemoirShareVideoJobCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = await memoir_share_video_jobs.create_share_video_job(
        db,
        utterance_id,
        current_user.id,
        req,
        allow_admin=is_admin_user(current_user),
    )
    return job


@router.get(
    "/projects/{project_id}/share-video-candidate",
    response_model=MemoirShareVideoCandidateLatestResponse,
)
async def get_mobile_memoir_share_video_candidate(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    candidate = await memoir_share_video_candidates.get_latest_share_video_candidate(
        db, user_id=current_user.id, project_id=project_id,
    )
    return MemoirShareVideoCandidateLatestResponse(candidate=candidate)


@router.post(
    "/projects/{project_id}/share-video-candidates/{candidate_id}/dismiss",
    response_model=MemoirShareVideoCandidateResponse,
)
async def dismiss_mobile_memoir_share_video_candidate(
    project_id: str,
    candidate_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_share_video_candidates.dismiss_share_video_candidate(
        db, user_id=current_user.id, project_id=project_id, candidate_id=candidate_id,
    )


@router.post(
    "/projects/{project_id}/share-video-candidates/{candidate_id}/create-video",
    response_model=MemoirShareVideoJobResponse,
    status_code=201,
)
async def create_mobile_memoir_share_video_candidate_job(
    project_id: str,
    candidate_id: str,
    req: MemoirShareVideoCandidateCreateVideoRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_share_video_candidates.create_candidate_share_video(
        db, user_id=current_user.id, project_id=project_id, candidate_id=candidate_id, req=req,
    )


@router.get("/share-video/jobs/{job_id}", response_model=MemoirShareVideoJobResponse)
async def get_mobile_memoir_share_video_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_share_video_jobs.get_share_video_job(
        db,
        job_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )


@router.delete("/share-video/jobs/{job_id}", status_code=204)
async def delete_mobile_memoir_share_video_job(
    job_id: str,
    req: MemoirShareVideoDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await memoir_share_video_jobs.delete_share_video_job(
        db, job_id, current_user.id, allow_admin=is_admin_user(current_user),
    )
    return Response(status_code=204)


@router.get(
    "/projects/{project_id}/share-video/jobs",
    response_model=MemoirShareVideoJobListResponse,
)
async def list_mobile_memoir_share_video_jobs(
    project_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_share_video_jobs.list_share_video_jobs(
        db,
        project_id=project_id,
        user_id=current_user.id,
        limit=limit,
        offset=offset,
        allow_admin=is_admin_user(current_user),
    )


_BYTE_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_RANGE_CHUNK_BYTES = 256 * 1024


def _byte_range(header: str, size: int) -> tuple[int, int] | None:
    match = _BYTE_RANGE.fullmatch(header.strip())
    if not match or match.groups() == ("", ""):
        return None
    first, last = match.groups()
    if not first:
        return max(size - int(last), 0), size - 1
    return int(first), min(int(last), size - 1) if last else size - 1


async def _read_file_range(path: Path, start: int, end: int):
    async with aiofiles.open(path, "rb") as file:
        await file.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = await file.read(min(_RANGE_CHUNK_BYTES, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


async def _share_video_file_response(
    request: Request,
    video: memoir_share_video_jobs.CompletedShareVideo,
    variant: str,
) -> Response:
    path, mime_type, title = video.path, video.output.mime_type, video.title
    if variant == "thumbnail":
        try:
            thumbnail = await memoir_share_video_jobs.rendering.share_video_thumbnail(path)
        except (memoir_share_video_jobs.rendering.ShareVideoGenerationError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=503, detail="视频缩略图暂时不可用") from exc
        return FileResponse(thumbnail, media_type="image/jpeg")
    if variant == "end":
        try:
            end_frame = await memoir_share_video_jobs.rendering.share_video_end_frame(path)
        except (memoir_share_video_jobs.rendering.ShareVideoGenerationError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=503, detail="视频结尾画面暂时不可用") from exc
        return FileResponse(end_frame, media_type="image/jpeg")
    if variant == "card":
        try:
            cover = await memoir_share_video_jobs.rendering.share_video_card_cover(path, video.hook_text)
        except (memoir_share_video_jobs.rendering.ShareVideoGenerationError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=503, detail="视频封面暂时不可用") from exc
        return FileResponse(cover, media_type="image/jpeg")
    # Starlette 0.35 serves only whole files; byte ranges let the video player stream.
    size = path.stat().st_size
    byte_range = _byte_range(request.headers.get("range", ""), size)
    if byte_range is None:
        response = FileResponse(
            path,
            media_type=mime_type,
            filename=memoir_share_video_jobs.share_video_download_filename(title),
        )
        response.headers["Accept-Ranges"] = "bytes"
        return response
    start, end = byte_range
    if start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    return StreamingResponse(
        _read_file_range(path, start, end),
        status_code=206,
        media_type=mime_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{size}",
        },
    )


@router.get("/share-video/jobs/{job_id}/download")
async def download_mobile_memoir_share_video_job(
    job_id: str,
    request: Request,
    variant: Literal["video", "thumbnail", "card", "end"] = "video",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    video = await memoir_share_video_jobs.load_completed_share_video_job(
        db,
        job_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    return await _share_video_file_response(request, video, variant)


@router.post("/share-video/shares/read", response_model=MemoirSharedVideoResponse)
async def read_mobile_memoir_shared_video(
    req: MemoirSharedVideoReadRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_share_video_shares.read_shared_video(db, req.code)


@router.get("/share-video/shares/{code}/video")
async def download_mobile_memoir_shared_video(
    code: str,
    request: Request,
    variant: Literal["video", "thumbnail", "card", "end"] = "video",
    db: AsyncSession = Depends(get_db),
):
    video = await memoir_share_video_shares.load_shared_video_file(db, code)
    return await _share_video_file_response(request, video, variant)


@router.patch("/voice-transcripts/{utterance_id}", response_model=MemoirVoiceTranscriptItemResponse)
async def update_mobile_memoir_voice_transcript(
    utterance_id: str,
    req: MemoirVoiceTranscriptUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_transcripts.update_voice_transcript(
        db,
        utterance_id,
        current_user.id,
        req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/voice-transcripts/{utterance_id}/recover-transcription",
    response_model=MemoirVoiceTranscriptItemResponse,
)
async def recover_mobile_memoir_voice_transcript(
    utterance_id: str,
    req: MemoirVoiceTranscriptRecoveryRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_transcript_recovery.recover_voice_transcript_from_audio(
        db,
        utterance_id,
        current_user.id,
        asr_client_factory=lambda: create_volc_streaming_asr_client(
            current_user.id,
            f"transcript-recovery-{utterance_id}-{uuid.uuid4().hex}",
        ),
        transcript_polish_level=(req.transcript_polish_level if req is not None else None),
        allow_admin=is_admin_user(current_user),
    )


@router.post("/voice-transcripts/{utterance_id}/cleanup", response_model=MemoirVoiceTranscriptItemResponse)
async def cleanup_mobile_memoir_voice_transcript(
    utterance_id: str,
    req: MemoirVoiceTranscriptCleanupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_voice_transcripts.cleanup_voice_transcript(
        db,
        utterance_id,
        current_user.id,
        req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/voice-transcripts/{utterance_id}/voice-edit/preview",
    response_model=MemoirVoiceTranscriptVoiceEditPreviewResponse,
)
async def preview_mobile_memoir_voice_transcript_voice_edit(
    utterance_id: str,
    req: MemoirVoiceTranscriptVoiceEditPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_transcript_voice_edit.preview_voice_transcript_edit(
        db,
        utterance_id,
        current_user.id,
        req,
    )


@router.delete("/voice-transcripts/{utterance_id}", status_code=204)
async def delete_mobile_memoir_voice_transcript(
    utterance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await memoir_voice_transcripts.delete_voice_transcript(db, utterance_id, current_user.id)
