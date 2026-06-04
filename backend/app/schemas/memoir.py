from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.memoir.types import (
    MemoirAnswerStatus,
    MemoirDraftStatus,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirSessionStatus,
)


class MemoirSessionCreate(BaseModel):
    title: str = "回忆录访谈"


class MemoirSessionResponse(BaseModel):
    id: str
    project_id: str
    title: str
    status: MemoirSessionStatus
    current_question_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirQuestionResponse(BaseModel):
    id: str
    session_id: str
    prompt: str
    source: MemoirQuestionSource
    sort_order: int
    status: MemoirQuestionStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AudioFileResponse(BaseModel):
    id: str
    mime_type: str
    size_bytes: int
    duration_seconds: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirAudioTranscriptionResponse(BaseModel):
    audio_file_id: str
    text: str
    usage: dict[str, object] | None = None


class MemoirTextAnswerCreate(BaseModel):
    question_id: str
    text: str
    audio_file_id: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)


class MemoirTextAnswerUpdate(BaseModel):
    text: str


class MemoirAnswerResponse(BaseModel):
    id: str
    question_id: str
    audio_file_id: str | None = None
    transcript_raw: str
    transcript_edited: str
    status: MemoirAnswerStatus
    duration_seconds: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirDraftGenerateRequest(BaseModel):
    ai_model: str | None = None


class MemoirDraftEditRequest(BaseModel):
    content_edited: str


class MemoirDraftResponse(BaseModel):
    id: str
    answer_id: str
    content_raw: str
    content_edited: str
    status: MemoirDraftStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirInterviewState(BaseModel):
    session: MemoirSessionResponse
    current_question: MemoirQuestionResponse | None = None
    completed_question_count: int = 0
    pending_question_count: int = 0
    latest_draft: MemoirDraftResponse | None = None
    questions: list[MemoirQuestionResponse] = Field(default_factory=list)
