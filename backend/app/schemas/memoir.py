from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.memoir.types import (
    MemoirAnswerStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCoverageStatus,
    MemoirCreatedBy,
    MemoirDraftStatus,
    MemoirGuidanceMode,
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
    MemoirVoiceSessionStatus,
)
from app.schemas.auth import UserResponse
from app.schemas.projects import ProjectResponse


class MemoirSessionCreate(BaseModel):
    title: str = "回忆录访谈"


class MemoirProjectCreate(BaseModel):
    title: str | None = None
    description: str | None = None
    elder_name: str | None = None
    narrator_gender: str | None = None
    narrator_origin: str | None = None
    narrator_age: int | None = None
    default_guidance_mode: MemoirGuidanceMode | None = None

    @field_validator("narrator_age")
    @classmethod
    def _valid_age(cls, value: int | None) -> int | None:
        if value is not None and (value < 0 or value > 130):
            raise ValueError("年龄需要在 0 到 130 岁之间")
        return value


class MemoirProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    elder_name: str | None = None
    narrator_gender: str | None = None
    narrator_origin: str | None = None
    narrator_age: int | None = None
    default_guidance_mode: MemoirGuidanceMode | None = None

    @field_validator("narrator_age")
    @classmethod
    def _valid_age(cls, value: int | None) -> int | None:
        if value is not None and (value < 0 or value > 130):
            raise ValueError("年龄需要在 0 到 130 岁之间")
        return value


class MemoirProjectListResponse(BaseModel):
    active_project_id: str
    active_project: ProjectResponse | None = None
    projects: list[ProjectResponse] = Field(default_factory=list)


class MemoirIdentityResponse(UserResponse):
    active_project_id: str
    active_project: ProjectResponse | None = None
    projects: list[ProjectResponse] = Field(default_factory=list)


class MemoirSessionResponse(BaseModel):
    id: str
    project_id: str
    title: str
    status: MemoirSessionStatus
    current_question_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirVoiceSessionCreate(BaseModel):
    memoir_session_id: str | None = None
    guidance_mode: MemoirGuidanceMode = MemoirGuidanceMode.FREE_NARRATION
    current_guidance_module: str | None = None
    provider: str | None = None
    input_format: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)


class MemoirVoiceSessionStopRequest(BaseModel):
    error_message: str | None = None


class MemoirVoiceFollowUpRequest(BaseModel):
    ai_model: str | None = None
    regenerate: bool = False


class MemoirAssistantTTSRequest(BaseModel):
    voice: str | None = None
    speed_ratio: float | None = Field(default=None, ge=0.5, le=2.0)
    force_regenerate: bool = False


class MemoirVoiceSessionResponse(BaseModel):
    id: str
    project_id: str
    memoir_session_id: str | None = None
    status: MemoirVoiceSessionStatus
    guidance_mode: MemoirGuidanceMode
    current_guidance_module: str | None = None
    provider: str | None = None
    input_format: str | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    error_message: str = ""
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirChatMessageResponse(BaseModel):
    id: str
    project_id: str
    voice_session_id: str
    role: MemoirChatMessageRole
    content_text: str
    content_status: MemoirChatContentStatus
    audio_file_id: str | None = None
    transcript_version_id: str | None = None
    parent_message_id: str | None = None
    guidance_intent: str | None = None
    turn_index: int
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirVoiceUtteranceResponse(BaseModel):
    id: str
    voice_session_id: str
    chat_message_id: str | None = None
    audio_file_id: str | None = None
    transcript_raw: str
    transcript_edited: str
    start_ms: int | None = None
    end_ms: int | None = None
    provider: str | None = None
    provider_payload_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirVoiceSessionDetailResponse(BaseModel):
    session: MemoirVoiceSessionResponse
    messages: list[MemoirChatMessageResponse] = Field(default_factory=list)
    utterances: list[MemoirVoiceUtteranceResponse] = Field(default_factory=list)


class MemoirVoiceSessionSummaryResponse(BaseModel):
    id: str
    project_id: str
    status: MemoirVoiceSessionStatus
    guidance_mode: MemoirGuidanceMode
    current_guidance_module: str | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    user_message_count: int
    assistant_message_count: int
    utterance_count: int
    total_user_char_count: int
    total_audio_seconds: int
    last_message_text: str
    last_message_role: MemoirChatMessageRole | None = None
    last_message_at: datetime | None = None


class MemoirVoiceSessionListResponse(BaseModel):
    items: list[MemoirVoiceSessionSummaryResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class MemoirVoiceModuleProgressResponse(BaseModel):
    module: str
    session_count: int
    utterance_count: int
    user_char_count: int


class MemoirVoiceProgressResponse(BaseModel):
    project_id: str | None = None
    session_count: int
    active_session_count: int
    stopped_session_count: int
    failed_session_count: int
    utterance_count: int
    user_message_count: int
    assistant_message_count: int
    total_user_char_count: int
    total_audio_seconds: int
    last_session_at: datetime | None = None
    modules: list[MemoirVoiceModuleProgressResponse] = Field(default_factory=list)


class MemoirVoiceFollowUpResponse(BaseModel):
    message: MemoirChatMessageResponse
    follow_up_text: str


class MemoirTTSAlignmentSegmentResponse(BaseModel):
    text: str
    start_ms: int
    end_ms: int
    text_start: int
    text_end: int
    confidence: float | None = None


class MemoirAssistantTTSResponse(BaseModel):
    message: MemoirChatMessageResponse
    audio_file_id: str
    provider: str
    model: str
    mime_type: str
    duration_seconds: float | None = None
    alignment_provider: str | None = None
    alignment: list[MemoirTTSAlignmentSegmentResponse] = Field(default_factory=list)


class MemoirVoiceTranscriptIncludedChapterResponse(BaseModel):
    chapter_id: str
    title: str
    sort_order: int


class MemoirVoiceTranscriptItemResponse(BaseModel):
    id: str
    voice_session_id: str
    project_id: str
    project_title: str
    chat_message_id: str | None = None
    audio_file_id: str | None = None
    audio_mime_type: str | None = None
    audio_size_bytes: int | None = None
    audio_duration_seconds: int | None = None
    transcript_version_id: str | None = None
    question_message_id: str | None = None
    question_text: str = ""
    question_audio_file_id: str | None = None
    transcript_raw: str
    transcript_edited: str
    display_text: str
    raw_char_count: int
    edited_char_count: int
    display_char_count: int
    start_ms: int | None = None
    end_ms: int | None = None
    provider: str | None = None
    session_status: MemoirVoiceSessionStatus
    guidance_mode: MemoirGuidanceMode
    current_guidance_module: str | None = None
    is_included_in_book: bool = False
    included_chapters: list[MemoirVoiceTranscriptIncludedChapterResponse] = Field(default_factory=list)
    created_at: datetime
    session_created_at: datetime


class MemoirVoiceTranscriptListResponse(BaseModel):
    items: list[MemoirVoiceTranscriptItemResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class MemoirVoiceTranscriptUpdateRequest(BaseModel):
    transcript_edited: str


class MemoirVoiceTranscriptCleanupRequest(BaseModel):
    ai_model: str | None = None


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


class MemoirMemoryUpdateRequest(BaseModel):
    status: MemoirMemoryStatus | None = None
    canonical_name: str | None = None
    aliases_json: list[str] | None = None
    relationship: str | None = None
    title: str | None = None
    life_stage: str | None = None
    time_text: str | None = None
    place_text: str | None = None
    cause: str | None = None
    process: str | None = None
    result: str | None = None
    emotion: str | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    name: str | None = None
    elder_alias: str | None = None
    modern_name: str | None = None
    place_type: str | None = None
    description: str | None = None
    strength: float | None = Field(default=None, ge=0, le=1)
    mention_count: int | None = Field(default=None, ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class MemoirOpenQuestionUpdateRequest(BaseModel):
    question: str | None = None
    reason: str | None = None
    sensitivity: MemoirSensitivity | None = None
    priority: float | None = Field(default=None, ge=0, le=1)
    status: MemoirOpenQuestionStatus | None = None


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


class MemoirCoverageItemResponse(BaseModel):
    topic_id: str
    key: str
    name: str
    life_stage: str | None = None
    description: str
    sort_order: int
    answered_count: int
    memory_count: int
    open_question_count: int
    coverage_score: float
    status: MemoirCoverageStatus
    signals_json: dict[str, object] = Field(default_factory=dict)
    calculated_at: datetime


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


class MemoirSessionProcessResponse(BaseModel):
    session_summary: MemoirSessionSummaryResponse
    story_fragment: MemoirOutputResponse | None = None
    people: list[MemoirPersonMemoryResponse] = Field(default_factory=list)
    events: list[MemoirEventMemoryResponse] = Field(default_factory=list)
    places: list[MemoirPlaceMemoryResponse] = Field(default_factory=list)
    themes: list[MemoirThemeMemoryResponse] = Field(default_factory=list)
    open_questions: list[MemoirOpenQuestionResponse] = Field(default_factory=list)


class MemoirMemoryStatsResponse(BaseModel):
    total_memory_count: int = 0
    draft_memory_count: int = 0
    confirmed_memory_count: int = 0
    rejected_memory_count: int = 0
    open_question_count: int = 0
    asked_open_question_count: int = 0
    answered_open_question_count: int = 0
    dismissed_open_question_count: int = 0
    question_candidate_count: int = 0
    output_count: int = 0
    coverage_average: int = 0
    low_coverage_count: int = 0


class MemoirDashboardResponse(BaseModel):
    project: ProjectResponse
    interview: MemoirInterviewState
    summary: MemoirSummaryResponse
    session_summary: MemoirSessionSummaryResponse | None = None
    people: list[MemoirPersonMemoryResponse] = Field(default_factory=list)
    events: list[MemoirEventMemoryResponse] = Field(default_factory=list)
    places: list[MemoirPlaceMemoryResponse] = Field(default_factory=list)
    themes: list[MemoirThemeMemoryResponse] = Field(default_factory=list)
    open_questions: list[MemoirOpenQuestionResponse] = Field(default_factory=list)
    coverage: list[MemoirCoverageItemResponse] = Field(default_factory=list)
    candidates: list[MemoirQuestionCandidateResponse] = Field(default_factory=list)
    outputs: list[MemoirOutputResponse] = Field(default_factory=list)
    memory_stats: MemoirMemoryStatsResponse = Field(default_factory=MemoirMemoryStatsResponse)
