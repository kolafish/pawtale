from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.memoir.types import (
    MemoirAnswerStatus,
    MemoirCoverageStatus,
    MemoirCreatedBy,
    MemoirDraftStatus,
    MemoirInterviewStage,
    MemoirMemoryStatus,
    MemoirMemoryType,
    MemoirOpenQuestionStatus,
    MemoirOutputStatus,
    MemoirOutputType,
    MemoirQuestionCandidateStatus,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirQuestionType,
    MemoirSensitivity,
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
    answer_id: str
    source_artifact_id: str | None = None
    version_type: MemoirTranscriptVersionType
    content: str
    provider: str | None = None
    confidence: float | None = None
    created_by: MemoirCreatedBy
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirPersonMemoryCreate(BaseModel):
    project_id: str
    canonical_name: str
    aliases_json: list[str] = Field(default_factory=list)
    relationship: str | None = None
    first_seen_session_id: str | None = None
    description: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: MemoirMemoryStatus = MemoirMemoryStatus.DRAFT
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI


class MemoirPersonMemoryResponse(BaseModel):
    id: str
    project_id: str
    canonical_name: str
    aliases_json: list[str] = Field(default_factory=list)
    relationship: str | None = None
    first_seen_session_id: str | None = None
    description: str
    confidence: float | None = None
    status: MemoirMemoryStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirEventMemoryCreate(BaseModel):
    project_id: str
    title: str
    life_stage: str | None = None
    time_text: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    place_text: str | None = None
    cause: str = ""
    process: str = ""
    result: str = ""
    emotion: str | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: MemoirMemoryStatus = MemoirMemoryStatus.DRAFT
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI


class MemoirEventMemoryResponse(BaseModel):
    id: str
    project_id: str
    title: str
    life_stage: str | None = None
    time_text: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    place_text: str | None = None
    cause: str
    process: str
    result: str
    emotion: str | None = None
    importance: float | None = None
    confidence: float | None = None
    status: MemoirMemoryStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirPlaceMemoryCreate(BaseModel):
    project_id: str
    name: str
    elder_alias: str | None = None
    modern_name: str | None = None
    place_type: str | None = None
    description: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: MemoirMemoryStatus = MemoirMemoryStatus.DRAFT
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI


class MemoirPlaceMemoryResponse(BaseModel):
    id: str
    project_id: str
    name: str
    elder_alias: str | None = None
    modern_name: str | None = None
    place_type: str | None = None
    description: str
    confidence: float | None = None
    status: MemoirMemoryStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirThemeMemoryCreate(BaseModel):
    project_id: str
    name: str
    description: str = ""
    strength: float | None = Field(default=None, ge=0, le=1)
    mention_count: int = Field(default=1, ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: MemoirMemoryStatus = MemoirMemoryStatus.DRAFT
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI


class MemoirThemeMemoryResponse(BaseModel):
    id: str
    project_id: str
    name: str
    description: str
    strength: float | None = None
    mention_count: int
    confidence: float | None = None
    status: MemoirMemoryStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirMemoryEvidenceCreate(BaseModel):
    project_id: str
    memory_type: MemoirMemoryType
    memory_id: str
    session_id: str | None = None
    question_id: str | None = None
    answer_id: str | None = None
    transcript_version_id: str | None = None
    quote: str = ""
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)


class MemoirMemoryEvidenceResponse(BaseModel):
    id: str
    project_id: str
    memory_type: MemoirMemoryType
    memory_id: str
    session_id: str | None = None
    question_id: str | None = None
    answer_id: str | None = None
    transcript_version_id: str | None = None
    quote: str
    start_offset: int | None = None
    end_offset: int | None = None
    confidence: float | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirOpenQuestionCreate(BaseModel):
    project_id: str
    session_id: str | None = None
    target_memory_type: MemoirMemoryType | None = None
    target_memory_id: str | None = None
    question: str
    question_type: MemoirQuestionType
    reason: str = ""
    sensitivity: MemoirSensitivity = MemoirSensitivity.LOW
    priority: float | None = Field(default=None, ge=0, le=1)
    status: MemoirOpenQuestionStatus = MemoirOpenQuestionStatus.OPEN
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI


class MemoirOpenQuestionResponse(BaseModel):
    id: str
    project_id: str
    session_id: str | None = None
    target_memory_type: str | None = None
    target_memory_id: str | None = None
    question: str
    question_type: MemoirQuestionType
    reason: str
    sensitivity: MemoirSensitivity
    priority: float | None = None
    status: MemoirOpenQuestionStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirCoverageTopicCreate(BaseModel):
    project_id: str
    key: str
    name: str
    life_stage: str | None = None
    description: str = ""
    sort_order: int = 0


class MemoirCoverageTopicResponse(BaseModel):
    id: str
    project_id: str
    key: str
    name: str
    life_stage: str | None = None
    description: str
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirCoverageSnapshotResponse(BaseModel):
    id: str
    project_id: str
    topic_id: str
    answered_count: int
    memory_count: int
    open_question_count: int
    coverage_score: float
    status: MemoirCoverageStatus
    signals_json: dict[str, object] = Field(default_factory=dict)
    calculated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirQuestionCandidateResponse(BaseModel):
    id: str
    project_id: str
    session_id: str | None = None
    question: str
    question_type: MemoirQuestionType
    target_memory_type: str | None = None
    target_memory_id: str | None = None
    coverage_topic_id: str | None = None
    reason: str
    sensitivity: MemoirSensitivity
    stage: MemoirInterviewStage
    priority: float
    status: MemoirQuestionCandidateStatus
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


class MemoirSessionProcessResponse(BaseModel):
    session_summary: MemoirSessionSummaryResponse
    story_fragment: MemoirOutputResponse | None = None
    people: list[MemoirPersonMemoryResponse] = Field(default_factory=list)
    events: list[MemoirEventMemoryResponse] = Field(default_factory=list)
    places: list[MemoirPlaceMemoryResponse] = Field(default_factory=list)
    themes: list[MemoirThemeMemoryResponse] = Field(default_factory=list)
    open_questions: list[MemoirOpenQuestionResponse] = Field(default_factory=list)
