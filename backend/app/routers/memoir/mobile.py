from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir import (
    AudioFileResponse,
    MemoirAudioTranscriptionResponse,
    MemoirAnswerResponse,
    MemoirDraftEditRequest,
    MemoirDraftGenerateRequest,
    MemoirDraftResponse,
    MemoirInterviewState,
    MemoirQuestionResponse,
    MemoirSessionCreate,
    MemoirStoryListResponse,
    MemoirSummaryResponse,
    MemoirTextAnswerCreate,
    MemoirTextAnswerUpdate,
)
from app.schemas.projects import ProjectResponse
from app.services.memoir import answers as memoir_answers
from app.services.memoir import audio_files as memoir_audio_files
from app.services.memoir import drafting as memoir_drafting
from app.services.memoir import interview as memoir_interview
from app.services.memoir import projects as memoir_projects
from app.services.memoir import story_cards as memoir_story_cards
from app.services.memoir import transcription as memoir_transcription


router = APIRouter()


async def _default_memoir_project_id(db: AsyncSession, user_id: str) -> str:
    project = await memoir_projects.get_or_create_mobile_memoir_project(db, user_id)
    return project.id


@router.get("/project", response_model=ProjectResponse)
async def get_mobile_memoir_project(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.get_or_create_mobile_memoir_project(db, current_user.id)


@router.post("/sessions", response_model=MemoirInterviewState, status_code=201)
async def create_mobile_memoir_session(
    req: MemoirSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _default_memoir_project_id(db, current_user.id)
    return await memoir_interview.create_session(db, project_id, current_user.id, req)


@router.get("/sessions/current", response_model=MemoirInterviewState)
async def get_current_mobile_memoir_session(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _default_memoir_project_id(db, current_user.id)
    return await memoir_interview.get_or_create_current_session(db, project_id, current_user.id)


@router.get("/questions/next", response_model=MemoirQuestionResponse | None)
async def get_next_mobile_memoir_question(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _default_memoir_project_id(db, current_user.id)
    return await memoir_interview.get_next_question(db, project_id, current_user.id)


@router.get("/summary", response_model=MemoirSummaryResponse)
async def get_mobile_memoir_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _default_memoir_project_id(db, current_user.id)
    return await memoir_story_cards.get_summary(db, project_id, current_user.id)


@router.get("/stories", response_model=MemoirStoryListResponse)
async def list_mobile_memoir_stories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _default_memoir_project_id(db, current_user.id)
    return await memoir_story_cards.list_story_cards(db, project_id, current_user.id)


@router.post("/questions/{question_id}/skip", response_model=MemoirInterviewState)
async def skip_mobile_memoir_question(
    question_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_interview.skip_question(db, question_id, current_user.id)


@router.post("/audio-files", response_model=AudioFileResponse, status_code=201)
async def upload_mobile_memoir_audio_file(
    file: UploadFile = File(...),
    duration_seconds: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_audio_files.save_audio_file(
        db,
        current_user.id,
        file,
        duration_seconds,
    )


@router.post("/audio-files/{audio_file_id}/transcribe", response_model=MemoirAudioTranscriptionResponse)
async def transcribe_mobile_memoir_audio_file(
    audio_file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_transcription.transcribe_audio_file(db, audio_file_id, current_user.id)


@router.post("/answers", response_model=MemoirAnswerResponse, status_code=201)
async def create_mobile_memoir_answer(
    req: MemoirTextAnswerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_answers.answer_question_with_text(
        db,
        req.question_id,
        current_user.id,
        req.text,
        audio_file_id=req.audio_file_id,
        duration_seconds=req.duration_seconds,
    )


@router.patch("/answers/{answer_id}", response_model=MemoirAnswerResponse)
async def update_mobile_memoir_answer(
    answer_id: str,
    req: MemoirTextAnswerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_answers.update_answer_text(db, answer_id, current_user.id, req.text)


@router.post("/answers/{answer_id}/draft", response_model=MemoirDraftResponse)
async def generate_mobile_memoir_draft(
    answer_id: str,
    req: MemoirDraftGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_drafting.generate_draft_for_answer(
        db,
        answer_id,
        current_user.id,
        req,
    )


@router.patch("/drafts/{draft_id}", response_model=MemoirDraftResponse)
async def update_mobile_memoir_draft(
    draft_id: str,
    req: MemoirDraftEditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_drafting.update_draft_content(db, draft_id, current_user.id, req)


@router.post("/drafts/{draft_id}/accept", response_model=MemoirDraftResponse)
async def accept_mobile_memoir_draft(
    draft_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_drafting.accept_draft(db, draft_id, current_user.id)
