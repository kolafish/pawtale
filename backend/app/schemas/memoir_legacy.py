from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.memoir.types import (
    MemoirAnswerStatus,
    MemoirCreatedBy,
    MemoirDraftStatus,
    MemoirOutputStatus,
    MemoirOutputType,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirSessionStatus,
    MemoirSourceArtifactType,
    MemoirTranscriptVersionType,
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

class MemoirSessionProcessRequest(BaseModel):
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

class MemoirStoryCardResponse(BaseModel):
    id: str
    answer_id: str
    question_id: str
    session_id: str
    session_title: str
    question_prompt: str
    audio_file_id: str | None = None
    answer_preview: str
    answer_status: MemoirAnswerStatus
    draft_id: str | None = None
    draft_status: MemoirDraftStatus | None = None
    draft_preview: str | None = None
    duration_seconds: int | None = None
    created_at: datetime
    updated_at: datetime

class MemoirStoryListResponse(BaseModel):
    stories: list[MemoirStoryCardResponse] = Field(default_factory=list)
    total: int = 0

class MemoirStoryDetailResponse(BaseModel):
    story: MemoirStoryCardResponse
    question: MemoirQuestionResponse
    answer: MemoirAnswerResponse
    draft: MemoirDraftResponse | None = None

class MemoirSummaryResponse(BaseModel):
    total_story_count: int = 0
    weekly_story_count: int = 0
    pending_review_count: int = 0
    confirmed_story_count: int = 0
    total_question_count: int = 0
    completed_question_count: int = 0
    progress_percent: int = 0

class MemoirInterviewState(BaseModel):
    session: MemoirSessionResponse
    current_question: MemoirQuestionResponse | None = None
    completed_question_count: int = 0
    pending_question_count: int = 0
    latest_draft: MemoirDraftResponse | None = None
    questions: list[MemoirQuestionResponse] = Field(default_factory=list)

class MemoirSourceArtifactCreate(BaseModel):
    project_id: str
    session_id: str | None = None
    answer_id: str | None = None
    artifact_type: MemoirSourceArtifactType
    storage_path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    metadata_json: dict[str, object] = Field(default_factory=dict)

class MemoirSourceArtifactResponse(BaseModel):
    id: str
    project_id: str
    session_id: str | None = None
    answer_id: str | None = None
    artifact_type: MemoirSourceArtifactType
    storage_path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirTranscriptVersionCreate(BaseModel):
    answer_id: str
    source_artifact_id: str | None = None
    version_type: MemoirTranscriptVersionType
    content: str
    provider: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    created_by: MemoirCreatedBy = MemoirCreatedBy.SYSTEM

class MemoirTranscriptVersionResponse(BaseModel):
    id: str
    answer_id: str | None = None
    voice_session_id: str | None = None
    source_artifact_id: str | None = None
    version_type: MemoirTranscriptVersionType
    content: str
    provider: str | None = None
    confidence: float | None = None
    created_by: MemoirCreatedBy
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirSessionSummaryResponse(BaseModel):
    id: str
    project_id: str
    session_id: str
    summary: str
    new_people_json: list[dict[str, object]] = Field(default_factory=list)
    new_events_json: list[dict[str, object]] = Field(default_factory=list)
    new_places_json: list[dict[str, object]] = Field(default_factory=list)
    new_themes_json: list[dict[str, object]] = Field(default_factory=list)
    open_questions_json: list[dict[str, object]] = Field(default_factory=list)
    next_question_suggestions_json: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirOutputResponse(BaseModel):
    id: str
    project_id: str
    output_type: MemoirOutputType
    title: str
    source_session_id: str | None = None
    status: MemoirOutputStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirOutputVersionResponse(BaseModel):
    id: str
    output_id: str
    version: int
    content: str
    model: str | None = None
    prompt_snapshot: str
    feedback: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirOutputDetailResponse(MemoirOutputResponse):
    versions: list[MemoirOutputVersionResponse] = Field(default_factory=list)
