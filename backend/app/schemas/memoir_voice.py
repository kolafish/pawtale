from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirGuidanceMode,
    MemoirInterviewType,
    MemoirSessionGoal,
    MemoirShareVideoJobStatus,
    MemoirShareVideoCandidateStatus,
    MemoirTranscriptPolishLevel,
    MemoirTranscriptionStatus,
    MemoirVoiceSessionStatus,
)
from app.schemas.memoir_automation import (
    MemoirAutomationEventAcknowledgeDismissibleResponse as MemoirAutomationEventAcknowledgeDismissibleResponse,
    MemoirAutomationEventListResponse as MemoirAutomationEventListResponse,
    MemoirAutomationEventResponse as MemoirAutomationEventResponse,
    MemoirAutomationEventVoicePlaybackRequest as MemoirAutomationEventVoicePlaybackRequest,
    MemoirAutomationEventVoicePlaybackResponse as MemoirAutomationEventVoicePlaybackResponse,
)
from app.services.memoir.share_video_styles import DEFAULT_SHARE_VIDEO_STYLE, ShareVideoStyle


class MemoirVoiceSessionCreate(BaseModel):
    guidance_mode: MemoirGuidanceMode = MemoirGuidanceMode.FREE_NARRATION
    interview_type: MemoirInterviewType = MemoirInterviewType.AI_INTERVIEW
    session_goal: MemoirSessionGoal = MemoirSessionGoal.OPEN_NARRATION
    current_completion_issue_id: str | None = Field(default=None, max_length=36)
    anchor_framework_item_id: str | None = Field(default=None, max_length=36)
    current_guidance_module: str | None = None
    provider: str | None = None
    input_format: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_interview_target(self):
        if self.current_completion_issue_id and self.session_goal != MemoirSessionGoal.BOOK_COMPLETION:
            raise ValueError("current_completion_issue_id requires book_completion session_goal")
        if self.anchor_framework_item_id and (
            self.guidance_mode != MemoirGuidanceMode.GUIDED_INTERVIEW
            or self.session_goal != MemoirSessionGoal.OPEN_NARRATION
        ):
            raise ValueError("anchor_framework_item_id requires guided_interview open_narration")
        if self.current_completion_issue_id and self.anchor_framework_item_id:
            raise ValueError("voice session cannot target a completion issue and framework item together")
        return self


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
    session_goal: MemoirSessionGoal
    current_completion_issue_id: str | None = None
    anchor_framework_item_id: str | None = None
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
    framework_item_id: str | None = None
    completion_issue_id: str | None = None
    policy_decision_id: str | None = None
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
    session_goal: MemoirSessionGoal
    current_completion_issue_id: str | None = None
    anchor_framework_item_id: str | None = None
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


class MemoirMemoryActivationTraceResponse(BaseModel):
    id: str
    project_id: str
    voice_session_id: str
    current_message_id: str | None = None
    current_utterance_id: str | None = None
    status: Literal["skipped", "succeeded", "failed"]
    candidate_count: int
    injected_count: int
    prompt_version: str
    requested_model: str | None = None
    resolved_model: str | None = None
    provider: str | None = None
    elapsed_ms: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoirMemoryActivationTraceDetailResponse(MemoirMemoryActivationTraceResponse):
    trace_json: dict[str, object] = Field(default_factory=dict)


class MemoirMemoryActivationTraceListResponse(BaseModel):
    items: list[MemoirMemoryActivationTraceResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class MemoirVoiceModuleProgressResponse(BaseModel):
    module: str
    session_count: int
    utterance_count: int
    user_char_count: int


class MemoirProjectProgressChapterResponse(BaseModel):
    chapter_id: str
    title: str
    sort_order: int
    include_in_book: bool = True
    source_count: int
    pending_source_count: int
    pending_completion_source_count: int
    pending_new_source_count: int
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


class MemoirVoiceTranscriptSectionAssignmentResponse(BaseModel):
    chapter_id: str
    chapter_title: str
    chapter_sort_order: int
    section_id: str
    section_title: str
    section_sort_order: int
    requires_user_assignment: bool = False


class MemoirMaterialOfflineASRRequest(BaseModel):
    ai_model: str | None = None
    transcript_polish_level: MemoirTranscriptPolishLevel | None = None

    @field_validator("ai_model")
    @classmethod
    def _normalize_ai_model(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None


class MemoirMaterialOfflineASRStateResponse(BaseModel):
    status: Literal["idle", "pending", "running", "succeeded", "failed"] = "idle"
    job_id: str | None = None
    can_start: bool = False
    blocked_reason: str = ""
    providers: list[str] = Field(default_factory=lambda: ["volc", "stepfun"])
    narrator_origin: str = ""
    dialect_code: str = ""
    dialect_label: str = ""
    dialect_strategy: str = "none"
    dialect_enhancement_enabled: bool = False
    last_applied_at: datetime | None = None
    error_message: str = ""


class MemoirVoiceTranscriptItemResponse(BaseModel):
    id: str
    voice_session_id: str
    project_id: str
    project_title: str
    chat_message_id: str | None = None
    chat_message_role: MemoirChatMessageRole | None = None
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
    transcription_status: MemoirTranscriptionStatus = MemoirTranscriptionStatus.READY
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
    section_assignment: MemoirVoiceTranscriptSectionAssignmentResponse | None = None
    offline_asr: MemoirMaterialOfflineASRStateResponse = Field(
        default_factory=MemoirMaterialOfflineASRStateResponse,
    )
    created_at: datetime
    session_created_at: datetime
    session_started_at: datetime | None = None


class MemoirVoiceTranscriptListResponse(BaseModel):
    items: list[MemoirVoiceTranscriptItemResponse] = Field(default_factory=list)
    empty_count: int = 0
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


class MemoirShareVideoBackgroundStyleResponse(BaseModel):
    value: str
    label: str
    description: str


class MemoirShareVideoBackgroundModeResponse(BaseModel):
    value: str
    label: str
    description: str


class MemoirShareVideoBackgroundSourceResponse(BaseModel):
    value: str
    label: str
    description: str


class MemoirShareVideoStyleResponse(BaseModel):
    value: ShareVideoStyle
    label: str
    description: str


class MemoirTranscriptShareVideoContextResponse(BaseModel):
    utterance_id: str
    audio_file_id: str
    latest_job_id: str | None = None
    latest_image_job_id: str | None = None
    transcript_source: str
    transcript_text: str
    text_hash: str
    duration_ms: int
    alignment_strategy: str
    alignment_confidence: float | None = None
    min_select_ms: int = 3000
    max_select_ms: int = 90000
    photo_min_ms: int = 3000
    photo_max_count: int = 20
    ai_background_enabled: bool = False
    default_video_style: ShareVideoStyle = DEFAULT_SHARE_VIDEO_STYLE
    video_styles: list[MemoirShareVideoStyleResponse] = Field(default_factory=list)
    default_background_source: str = "ai"
    default_background_style: str = "watercolor"
    default_background_mode: str = "auto"
    background_sources: list[MemoirShareVideoBackgroundSourceResponse] = Field(default_factory=list)
    background_styles: list[MemoirShareVideoBackgroundStyleResponse] = Field(default_factory=list)
    background_modes: list[MemoirShareVideoBackgroundModeResponse] = Field(default_factory=list)
    segments: list[MemoirTranscriptShareVideoSegmentResponse] = Field(default_factory=list)


class MemoirShareVideoJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    ai_model: str | None = Field(default=None, max_length=120)
    video_style: ShareVideoStyle | None = None
    background_source: str | None = Field(default=None, max_length=40)
    background_style: str | None = Field(default=None, max_length=40)
    background_mode: str | None = Field(default=None, max_length=40)
    photo_asset_ids: list[str] = Field(default_factory=list, max_length=20)
    # A verbatim sentence from the selection shown before the narration; empty lets the title step pick.
    hook_text: str = Field(default="", max_length=200)
    # Who the people are (gender, age, relationships), written by the author for the illustrations.
    background_note: str = Field(default="", max_length=300)

    @field_validator(
        "ai_model",
        "background_source",
        "background_style",
        "background_mode",
    )
    @classmethod
    def _valid_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _valid_range(self) -> "MemoirShareVideoJobCreateRequest":
        if self.end_char <= self.start_char:
            raise ValueError("结束位置必须大于开始位置")
        return self


class MemoirShareVideoCandidateCreateVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_model: str | None = Field(default=None, max_length=120)
    video_style: ShareVideoStyle | None = None
    background_source: str | None = Field(default=None, max_length=40)
    background_style: str | None = Field(default=None, max_length=40)
    background_mode: str | None = Field(default=None, max_length=40)
    photo_asset_ids: list[str] = Field(default_factory=list, max_length=20)
    # None keeps the recommendation's own opening line; "" lets the title step pick.
    hook_text: str | None = Field(default=None, max_length=200)
    background_note: str = Field(default="", max_length=300)


class MemoirShareVideoCandidateResponse(BaseModel):
    id: str
    project_id: str
    voice_session_id: str
    utterance_id: str | None
    audio_file_id: str | None
    transcript_version_id: str | None
    text_hash: str | None
    selected_text: str
    start_char: int | None
    end_char: int | None
    start_ms: int | None
    end_ms: int | None
    duration_ms: int | None
    status: MemoirShareVideoCandidateStatus
    # Why the model thinks this range is worth a video, shown on the card.
    reason: str = ""
    hook_text: str = ""
    video_job_id: str | None
    created_at: datetime
    updated_at: datetime


class MemoirShareVideoCandidateLatestResponse(BaseModel):
    candidate: MemoirShareVideoCandidateResponse | None


class MemoirShareVideoOutputResponse(BaseModel):
    mime_type: str
    size_bytes: int
    download_url: str
    # Opens the public playback page from a forwarded card.
    share_code: str


class MemoirSharedVideoReadRequest(BaseModel):
    code: str


class MemoirSharedVideoResponse(BaseModel):
    title: str
    book_title: str
    narrator_name: str
    duration_ms: int
    video_url: str
    poster_url: str
    # The closing frame with the mini program code, shown once playback ends.
    end_frame_url: str
    # Present only while the author keeps the whole book shared.
    book_share_token: str | None = None


class MemoirShareVideoCountResponse(BaseModel):
    utterance_id: str
    count: int = Field(ge=1)
    job_ids: list[str] = Field(min_length=1)
    job_titles: dict[str, str] = Field(default_factory=dict)
    current_job_ids: list[str] = Field(default_factory=list)


class MemoirShareVideoListItemResponse(BaseModel):
    id: str
    utterance_id: str
    title: str
    selected_text: str
    duration_ms: int
    video_style: ShareVideoStyle = DEFAULT_SHARE_VIDEO_STYLE
    # The illustration style an AI-illustrated video was made in; empty otherwise.
    background_style_label: str = ""
    created_at: datetime
    completed_at: datetime | None
    share_code: str
    like_count: int = 0


class MemoirShareVideoDeleteRequest(BaseModel):
    confirm: Literal[True]


class MemoirShareVideoJobResponse(BaseModel):
    id: str
    project_id: str
    utterance_id: str
    audio_file_id: str
    status: MemoirShareVideoJobStatus
    title: str
    selected_text: str
    start_char: int
    end_char: int
    start_ms: int
    end_ms: int
    duration_ms: int
    video_style: ShareVideoStyle = DEFAULT_SHARE_VIDEO_STYLE
    background_source: str = "ai"
    background_style: str = "watercolor"
    background_mode: str = "auto"
    background_effective_mode: str = "single"
    photo_asset_ids: list[str] = Field(default_factory=list)
    background_note: str = ""
    background_style_label: str = ""
    background_scene_count: int = 0
    background_status: str = "disabled"
    background_provider: str | None = None
    background_model: str | None = None
    background_cache_hit: bool | None = None
    background_error_message: str = ""
    output: MemoirShareVideoOutputResponse | None
    error_message: str = ""
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class MemoirShareVideoJobListResponse(BaseModel):
    items: list[MemoirShareVideoListItemResponse] = Field(default_factory=list)
    active_jobs: list[MemoirShareVideoJobResponse] = Field(default_factory=list)
    failed_jobs: list[MemoirShareVideoJobResponse] = Field(default_factory=list)
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
    transcript_polish_level: MemoirTranscriptPolishLevel | None = None


class MemoirVoiceTranscriptRecoveryRequest(BaseModel):
    transcript_polish_level: MemoirTranscriptPolishLevel | None = None


class MemoirVoiceTranscriptVoiceEditPreviewRequest(BaseModel):
    instruction_text: str = Field(min_length=1, max_length=800)
    draft_text: str | None = None
    ai_model: str | None = None

    @field_validator("instruction_text")
    @classmethod
    def _non_empty_instruction(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("draft_text")
    @classmethod
    def _non_empty_draft(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized


class MemoirVoiceTranscriptVoiceEditChangeResponse(BaseModel):
    scope: Literal["term", "sentence", "passage"] = "passage"
    before: str = ""
    after: str = ""


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
