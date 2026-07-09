from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirGuidanceMode,
    MemoirInterviewType,
    MemoirVoiceSessionStatus,
)


class MemoirVoiceSessionCreate(BaseModel):
    guidance_mode: MemoirGuidanceMode = MemoirGuidanceMode.FREE_NARRATION
    interview_type: MemoirInterviewType = MemoirInterviewType.AI_INTERVIEW
    current_guidance_module: str | None = None
    provider: str | None = None
    input_format: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)

class MemoirVoiceSessionStopRequest(BaseModel):
    error_message: str | None = None

class MemoirVoiceAuthorizationConsentRequest(BaseModel):
    version: str
    agreed: bool
    source: str = "miniprogram"
    source_session_id: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("version", "source")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("source_session_id")
    @classmethod
    def _optional_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

class MemoirVoiceAuthorizationConsentResponse(BaseModel):
    id: str
    user_id: str
    consent_type: str
    version: str
    agreed: bool
    source: str
    source_session_id: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirVoiceAuthorizationConsentStatusResponse(BaseModel):
    has_current_consent: bool
    consent: MemoirVoiceAuthorizationConsentResponse | None = None


class MemoirVoiceNoAnswerPromptRequest(BaseModel):
    attempt: int = Field(default=1, ge=1, le=3)


class MemoirAssistantTTSRequest(BaseModel):
    voice: str | None = None
    speed_ratio: float | None = Field(default=None, ge=0.5, le=2.0)
    force_regenerate: bool = False


class MemoirVoiceSessionResponse(BaseModel):
    id: str
    project_id: str
    status: MemoirVoiceSessionStatus
    guidance_mode: MemoirGuidanceMode
    interview_type: MemoirInterviewType
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
    source_material_type: str = ""
    source_route_action: str = ""
    source_route_confidence: float | None = None
    source_route_reason: str = ""
    source_route_updated_at: datetime | None = None
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
    interview_type: MemoirInterviewType
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


class MemoirAutomationEventUtteranceDetailResponse(BaseModel):
    utterance_id: str
    source_index: int | None = None
    record_title: str
    preview_text: str
    chapter_id: str | None = None
    chapter_title: str | None = None
    created_at: datetime | None = None


class MemoirAutomationEventResponse(BaseModel):
    id: str
    project_id: str
    event_type: MemoirAutomationEventType
    status: MemoirAutomationEventStatus
    chapter_id: str | None = None
    utterance_ids: list[str] = Field(default_factory=list)
    summary_text: str
    confidence: float | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    utterance_details: list[MemoirAutomationEventUtteranceDetailResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirAutomationEventListResponse(BaseModel):
    items: list[MemoirAutomationEventResponse] = Field(default_factory=list)
    total: int
    limit: int


class MemoirAutomationEventVoicePlaybackRequest(BaseModel):
    event_ids: list[str] = Field(default_factory=list, max_length=20)
    voice_session_id: str
    message_id: str


class MemoirAutomationEventVoicePlaybackResponse(BaseModel):
    updated_count: int


class MemoirProjectProgressChapterResponse(BaseModel):
    chapter_id: str
    title: str
    sort_order: int
    source_count: int
    pending_source_count: int
    removed_source_count: int
    draft_status: str | None = None
    draft_word_count: int = 0


class MemoirProjectProgressSnapshotResponse(BaseModel):
    project_id: str
    valid_utterance_count: int
    assigned_utterance_count: int
    pending_utterance_count: int
    session_count: int
    active_session_count: int
    stopped_session_count: int
    failed_session_count: int
    user_message_count: int
    assistant_message_count: int
    source_char_count: int
    total_audio_seconds: int
    chapter_count: int
    confirmed_chapter_count: int
    draft_word_count: int
    last_session_at: datetime | None = None
    recent_automation_events: list[MemoirAutomationEventResponse] = Field(default_factory=list)
    chapter_progress_items: list[MemoirProjectProgressChapterResponse] = Field(default_factory=list)


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
    snapshot: MemoirProjectProgressSnapshotResponse | None = None

class MemoirVoiceFollowUpResponse(BaseModel):
    message: MemoirChatMessageResponse
    follow_up_text: str
    primary_intent: str = "follow_up"
    intent_tags: list[str] = Field(default_factory=list)
    expects_answer: bool = True


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
    summary_title: str = ""
    summary_text: str = ""
    summary_source: str = "fallback"
    transcription_status: str = "ready"
    can_recover_transcription: bool = False
    raw_char_count: int
    edited_char_count: int
    display_char_count: int
    start_ms: int | None = None
    end_ms: int | None = None
    provider: str | None = None
    session_status: MemoirVoiceSessionStatus
    guidance_mode: MemoirGuidanceMode
    interview_type: MemoirInterviewType = MemoirInterviewType.AI_INTERVIEW
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


class MemoirTranscriptShareVideoSegmentResponse(BaseModel):
    index: int
    text: str
    start_char: int
    end_char: int
    start_ms: int
    end_ms: int
    confidence: float | None = None
    selectable: bool = True


class MemoirTranscriptShareVideoContextResponse(BaseModel):
    utterance_id: str
    audio_file_id: str
    transcript_source: str
    transcript_text: str
    text_hash: str
    duration_ms: int
    alignment_strategy: str
    alignment_confidence: float | None = None
    min_select_ms: int = 3000
    max_select_ms: int = 90000
    segments: list[MemoirTranscriptShareVideoSegmentResponse] = Field(default_factory=list)

class MemoirVoiceSearchTranscriptionRequest(BaseModel):
    audio_base64: str = Field(min_length=1)
    mime_type: str = "audio/pcm"

    @field_validator("audio_base64", "mime_type")
    @classmethod
    def _non_empty_voice_search_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

class MemoirVoiceSearchTranscriptionResponse(BaseModel):
    transcript: str

class MemoirVoiceSearchQueryParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=240)
    ai_model: str | None = None

    @field_validator("text")
    @classmethod
    def _non_empty_search_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

class MemoirVoiceSearchQueryFiltersResponse(BaseModel):
    included: str = "all"
    guidance_mode: str | None = None
    module: str | None = None

class MemoirVoiceSearchQueryParseResponse(BaseModel):
    original_text: str
    query: str
    keywords: list[str] = Field(default_factory=list)
    filters: MemoirVoiceSearchQueryFiltersResponse
    display_text: str
    used_ai: bool = False
    provider: str | None = None
    model: str | None = None

class MemoirVoiceTranscriptUpdateRequest(BaseModel):
    transcript_edited: str

class MemoirVoiceTranscriptCleanupRequest(BaseModel):
    ai_model: str | None = None


class MemoirVoiceTranscriptVoiceEditPreviewRequest(BaseModel):
    instruction_text: str = Field(min_length=1, max_length=800)
    ai_model: str | None = None

    @field_validator("instruction_text")
    @classmethod
    def _non_empty_instruction(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized


class MemoirVoiceTranscriptVoiceEditChangeResponse(BaseModel):
    before: str = ""
    after: str = ""
    reason: str = ""


class MemoirVoiceTranscriptVoiceEditPreviewResponse(BaseModel):
    utterance_id: str
    action: str
    original_text: str
    current_text: str
    edited_text: str
    changes: list[MemoirVoiceTranscriptVoiceEditChangeResponse] = Field(default_factory=list)
    confidence: float
    requires_confirmation: bool = True
    reply_text: str
    provider: str | None = None
    model: str | None = None
