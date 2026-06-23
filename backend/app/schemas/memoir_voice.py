from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirGuidanceMode,
    MemoirVoiceSessionStatus,
)


class MemoirVoiceSessionCreate(BaseModel):
    guidance_mode: MemoirGuidanceMode = MemoirGuidanceMode.FREE_NARRATION
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

class MemoirVoiceFollowUpRequest(BaseModel):
    ai_model: str | None = None
    regenerate: bool = False

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
