from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.schemas.memoir_voice import (
    MemoirAutomationEventResponse,
    MemoirTTSAlignmentSegmentResponse,
)


class MemoirBookChapterSourceRef(BaseModel):
    voice_session_id: str
    message_id: str
    utterance_id: str
    transcript_version_id: str | None = None
    audio_file_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    created_at: datetime | None = None
    quote: str = ""


class MemoirBookSourceIncludedChapter(BaseModel):
    chapter_id: str
    title: str
    sort_order: int
    include_in_book: bool = True


class MemoirBookPlanChapterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    include_in_book: bool = True
    source_refs: list[MemoirBookChapterSourceRef] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("章节标题不能为空")
        if len(title) > 80:
            raise ValueError("章节标题不能超过 80 个字")
        return title


class MemoirBookPlanGenerateRequest(BaseModel):
    ai_model: str | None = None
    max_chapters: int = 8

    @field_validator("max_chapters")
    @classmethod
    def _valid_max_chapters(cls, value: int) -> int:
        if value < 1 or value > 12:
            raise ValueError("章节数量需要在 1 到 12 之间")
        return value


class MemoirBookPlanConfirmRequest(BaseModel):
    title: str | None = None
    chapters: list[MemoirBookPlanChapterInput]

    @field_validator("title")
    @classmethod
    def _valid_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            return None
        if len(title) > 120:
            raise ValueError("规划标题不能超过 120 个字")
        return title


class MemoirBookPlanChapterUpdateInput(MemoirBookPlanChapterInput):
    id: str | None = None

    @field_validator("id")
    @classmethod
    def _valid_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        chapter_id = value.strip()
        return chapter_id or None


class MemoirBookPlanUpdateRequest(BaseModel):
    title: str | None = None
    chapters: list[MemoirBookPlanChapterUpdateInput]

    @field_validator("title")
    @classmethod
    def _valid_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            return None
        if len(title) > 120:
            raise ValueError("规划标题不能超过 120 个字")
        return title


class MemoirBookPlanStructureCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "create_chapter_from_source",
        "delete_chapter",
        "merge_chapters",
        "move_chapter",
        "rename_chapter",
        "set_chapter_book_inclusion",
        "split_chapter",
    ]
    chapter_id: str | None = None
    target_chapter_id: str | None = None
    direction: Literal["up", "down"] | None = None
    target_position: Literal["first", "last"] | None = None
    target_index: int | None = Field(default=None, ge=1, le=99)
    relative_chapter_id: str | None = None
    relative_position: Literal["before", "after"] | None = None
    title: str | None = None
    include_in_book: bool | None = None
    utterance_id: str | None = None
    utterance_ids: list[str] = Field(default_factory=list)

    @field_validator("chapter_id", "target_chapter_id", "relative_chapter_id", "utterance_id")
    @classmethod
    def _valid_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("title")
    @classmethod
    def _valid_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            return None
        if len(title) > 80:
            raise ValueError("章节标题不能超过 80 个字")
        return title

    @field_validator("utterance_ids")
    @classmethod
    def _valid_utterance_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class MemoirBookVoiceCommandContextMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=240)
    intent: str | None = None
    summary: str | None = None

    @field_validator("text")
    @classmethod
    def _valid_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("对话上下文不能为空")
        return text

    @field_validator("intent", "summary")
    @classmethod
    def _valid_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MemoirBookVoiceCommandSourceCoverageContext(BaseModel):
    total_source_count: int = Field(default=0, ge=0)
    covered_source_count: int = Field(default=0, ge=0)
    uncovered_source_count: int = Field(default=0, ge=0)


class MemoirBookVoiceCommandUncoveredSourceContext(BaseModel):
    source_index: int = Field(ge=1)
    utterance_id: str
    text: str = Field(default="", max_length=180)

    @field_validator("utterance_id", "text")
    @classmethod
    def _valid_text(cls, value: str) -> str:
        return value.strip()


class MemoirBookVoiceCommandBookContext(BaseModel):
    source_coverage: MemoirBookVoiceCommandSourceCoverageContext | None = None
    uncovered_sources: list[MemoirBookVoiceCommandUncoveredSourceContext] = Field(default_factory=list, max_length=12)


class MemoirBookVoiceCommandParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=240)
    current_chapter_id: str | None = None
    conversation_context: list[MemoirBookVoiceCommandContextMessage] = Field(default_factory=list, max_length=8)
    book_context: MemoirBookVoiceCommandBookContext | None = None
    ai_model: str | None = None

    @field_validator("text")
    @classmethod
    def _valid_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("语音指令不能为空")
        return text

    @field_validator("current_chapter_id", "ai_model")
    @classmethod
    def _valid_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MemoirBookVoiceCommandOperationResponse(BaseModel):
    type: str
    chapter_id: str | None = None
    chapter_title: str | None = None
    target_chapter_id: str | None = None
    target_chapter_title: str | None = None
    new_title: str | None = None
    direction: str | None = None
    target_position: str | None = None
    target_index: int | None = None
    relative_chapter_id: str | None = None
    relative_chapter_title: str | None = None
    relative_position: str | None = None
    source_position: int | None = None
    source_index: int | None = None
    utterance_id: str | None = None


class MemoirBookVoiceCommandParseResponse(BaseModel):
    original_text: str
    intent: str
    intent_label: str
    response_kind: Literal["action_preview", "consultation", "answer", "clarification"] = "action_preview"
    summary: str
    reply_text: str | None = None
    operations: list[MemoirBookVoiceCommandOperationResponse] = Field(default_factory=list)
    requires_confirmation: bool = True
    used_ai: bool = False
    provider: str | None = None
    model: str | None = None


class MemoirBookVoiceCommandExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    operation: MemoirBookVoiceCommandOperationResponse

    @field_validator("intent")
    @classmethod
    def _valid_intent(cls, value: str) -> str:
        return value.strip()


class MemoirBookChapterTitleUpdateRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("章节标题不能为空")
        if len(title) > 80:
            raise ValueError("章节标题不能超过 80 个字")
        return title


class MemoirBookChapterTitleGenerateRequest(BaseModel):
    ai_model: str | None = None


class MemoirBookPlanChapterResponse(BaseModel):
    id: str
    plan_id: str
    project_id: str
    title: str
    sort_order: int
    include_in_book: bool = True
    source_refs: list[MemoirBookChapterSourceRef] = Field(default_factory=list)
    metadata_json: dict[str, object] = Field(default_factory=dict)
    pending_source_count: int = 0
    removed_source_count: int = 0
    created_at: datetime
    updated_at: datetime


class MemoirBookPlanResponse(BaseModel):
    id: str
    project_id: str
    title: str
    status: MemoirBookPlanStatus
    created_by: MemoirCreatedBy
    model: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    chapters: list[MemoirBookPlanChapterResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirBookSourceRouteRequest(BaseModel):
    utterance_id: str

    @field_validator("utterance_id")
    @classmethod
    def _valid_utterance_id(cls, value: str) -> str:
        utterance_id = value.strip()
        if not utterance_id:
            raise ValueError("讲述记录不能为空")
        return utterance_id


class MemoirBookSourceRouteEvidence(BaseModel):
    chapter_id: str | None = None
    utterance_id: str | None = None
    quote: str
    reason: str = ""


class MemoirBookSourceRouteResponse(BaseModel):
    material_type: Literal["memoir_content", "ai_interaction", "meta_instruction", "noise", "too_short"]
    action: Literal[
        "assign_to_chapter",
        "suggest_new_chapter",
        "hold_for_review",
        "ignore",
    ]
    utterance_id: str
    source_index: int
    target_chapter_id: str | None = None
    target_chapter_title: str | None = None
    suggested_title: str | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str
    assignment_basis: Literal["ai", "completion_item"] = "ai"
    evidence: list[MemoirBookSourceRouteEvidence] = Field(default_factory=list)


class MemoirBookSourceAutoAssignmentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence_threshold: float = Field(default=0.53, ge=0.52, le=0.95)
    max_sources: int = Field(default=20, ge=1, le=100)
    ai_model: str | None = None

    @field_validator("ai_model")
    @classmethod
    def _valid_ai_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MemoirBookSourceAutoAssignmentItemResponse(BaseModel):
    utterance_id: str
    source_index: int
    material_type: Literal["memoir_content", "ai_interaction", "meta_instruction", "noise", "too_short"]
    action: Literal[
        "assign_to_chapter",
        "suggest_new_chapter",
        "hold_for_review",
        "ignore",
    ]
    target_chapter_id: str | None = None
    target_chapter_title: str | None = None
    confidence: float | None = None
    reason: str
    assigned: bool = False
    skip_reason: str = ""


class MemoirBookSourceAutoAssignmentRunResponse(BaseModel):
    project_id: str
    confidence_threshold: float
    evaluated_source_count: int
    assigned_source_count: int
    skipped_source_count: int
    items: list[MemoirBookSourceAutoAssignmentItemResponse] = Field(default_factory=list)
    events: list[MemoirAutomationEventResponse] = Field(default_factory=list)
    narrative_ingestion_job_ids: list[str] = Field(default_factory=list)
    plan: MemoirBookPlanResponse


class MemoirBookQualityAuditRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_event: bool = True
    semantic_audit: bool = False
    ai_model: str | None = None


class MemoirBookQualityAuditChapterResponse(BaseModel):
    chapter_id: str
    title: str
    sort_order: int
    source_count: int
    pending_source_count: int
    draft_word_count: int
    status: Literal[
        "needs_sources",
        "needs_draft",
        "needs_update",
        "thin_sources",
        "thin_draft",
        "healthy",
    ]
    attention: Literal["high", "medium", "low"]
    reason: str
    interview_hint: str


class MemoirBookQualityAuditDisplayResponse(BaseModel):
    title: str
    summary: str
    detail: str = ""


class MemoirBookQualityAuditPrimaryActionResponse(BaseModel):
    type: Literal[
        "generate_plan",
        "assign_sources",
        "continue_interview",
        "open_chapter_workspace",
        "review_book",
        "none",
    ]
    cta_text: str
    chapter_id: str | None = None
    utterance_count: int = 0


class MemoirBookQualityAuditPrimaryChapterResponse(BaseModel):
    chapter_id: str
    title: str
    status: Literal[
        "needs_sources",
        "needs_draft",
        "needs_update",
        "thin_sources",
        "thin_draft",
        "healthy",
    ]
    attention: Literal["high", "medium", "low"]
    reason: str


class MemoirBookQualityAuditRunResponse(BaseModel):
    project_id: str
    overall_status: Literal[
        "needs_plan",
        "needs_sources",
        "needs_drafts",
        "needs_updates",
        "collecting",
        "healthy",
    ]
    summary_text: str
    next_actions: list[str] = Field(default_factory=list)
    chapter_items: list[MemoirBookQualityAuditChapterResponse] = Field(default_factory=list)
    display: MemoirBookQualityAuditDisplayResponse
    primary_action: MemoirBookQualityAuditPrimaryActionResponse
    primary_chapter: MemoirBookQualityAuditPrimaryChapterResponse | None = None
    event: MemoirAutomationEventResponse | None = None


class MemoirSemanticCacheStatusCountsResponse(BaseModel):
    ready: int = 0
    stale: int = 0
    failed: int = 0
    missing_current_ready: int = 0


class MemoirSemanticChapterSourceStateResponse(BaseModel):
    chapter_count: int = 0
    active_link_count: int = 0
    removed_link_count: int = 0


class MemoirSemanticAuditSnapshotLatestResponse(BaseModel):
    snapshot_id: str
    engine: str
    overall_status: str = ""
    summary_text: str = ""
    error_message: str = ""
    updated_at: datetime


class MemoirSemanticAuditSnapshotStateResponse(BaseModel):
    ready: int = 0
    stale: int = 0
    failed: int = 0
    by_engine_status: dict[str, int] = Field(default_factory=dict)
    latest: list[MemoirSemanticAuditSnapshotLatestResponse] = Field(default_factory=list)


class MemoirSemanticFollowUpSuggestionResponse(BaseModel):
    source: Literal["chapter_card", "audit_snapshot"]
    chapter_id: str | None = None
    chapter_title: str = ""
    question: str
    reason: str = ""
    priority: Literal["high", "medium", "low"] = "medium"
    card_id: str | None = None
    snapshot_id: str | None = None


class MemoirSemanticStateFailureResponse(BaseModel):
    chapter_id: str | None = None
    error: str


class MemoirSemanticStateResponse(BaseModel):
    project_id: str
    title: str
    inspected_at: datetime
    voice_utterance_count: int = 0
    nonempty_utterance_count: int = 0
    source_cards: MemoirSemanticCacheStatusCountsResponse
    chapter_sources: MemoirSemanticChapterSourceStateResponse
    chapter_cards: MemoirSemanticCacheStatusCountsResponse
    audit_snapshots: MemoirSemanticAuditSnapshotStateResponse
    latest_audit_event_ids: list[str] = Field(default_factory=list)
    follow_up_suggestions: list[MemoirSemanticFollowUpSuggestionResponse] = Field(default_factory=list)
    failure_count: int = 0
    failures: list[MemoirSemanticStateFailureResponse] = Field(default_factory=list)


class MemoirBookSourceCoverageItem(BaseModel):
    source_index: int
    voice_session_id: str
    message_id: str
    utterance_id: str
    transcript_version_id: str | None = None
    audio_file_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    text: str
    created_at: datetime
    source_material_type: str = ""
    source_route_action: str = ""
    source_route_confidence: float | None = None
    source_route_reason: str = ""
    source_route_updated_at: datetime | None = None
    is_included: bool = False
    included_in_chapters: list[MemoirBookSourceIncludedChapter] = Field(default_factory=list)


class MemoirBookSourceCoverageResponse(BaseModel):
    project_id: str
    plan_id: str | None = None
    total_source_count: int
    covered_source_count: int
    uncovered_source_count: int
    sources: list[MemoirBookSourceCoverageItem] = Field(default_factory=list)
    uncovered_sources: list[MemoirBookSourceCoverageItem] = Field(default_factory=list)


class MemoirBookVoiceCommandExecuteResponse(BaseModel):
    intent: str
    summary: str
    plan: MemoirBookPlanResponse
    source_coverage: MemoirBookSourceCoverageResponse | None = None


class MemoirBookSourceAssignmentInput(BaseModel):
    chapter_id: str
    utterance_ids: list[str]

    @field_validator("chapter_id")
    @classmethod
    def _valid_chapter_id(cls, value: str) -> str:
        chapter_id = value.strip()
        if not chapter_id:
            raise ValueError("章节不能为空")
        return chapter_id

    @field_validator("utterance_ids")
    @classmethod
    def _valid_utterance_ids(cls, value: list[str]) -> list[str]:
        utterance_ids = [item.strip() for item in value if item.strip()]
        if not utterance_ids:
            raise ValueError("至少选择一条讲述记录")
        return list(dict.fromkeys(utterance_ids))


class MemoirBookSourceAssignmentRequest(BaseModel):
    ai_model: str | None = None
    assignments: list[MemoirBookSourceAssignmentInput]

    @field_validator("assignments")
    @classmethod
    def _valid_assignments(cls, value: list[MemoirBookSourceAssignmentInput]) -> list[MemoirBookSourceAssignmentInput]:
        if not value:
            raise ValueError("至少需要一条归入操作")
        return value


class MemoirBookSourceMoveRequest(BaseModel):
    ai_model: str | None = None
    source_chapter_id: str
    target_chapter_id: str
    utterance_id: str

    @field_validator("source_chapter_id", "target_chapter_id", "utterance_id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        item_id = value.strip()
        if not item_id:
            raise ValueError("移动素材参数不能为空")
        return item_id

    @model_validator(mode="after")
    def _valid_distinct_chapters(self) -> "MemoirBookSourceMoveRequest":
        if self.source_chapter_id == self.target_chapter_id:
            raise ValueError("目标章节不能与原章节相同")
        return self


class MemoirBookSourceRemoveRequest(BaseModel):
    ai_model: str | None = None
    chapter_id: str
    utterance_ids: list[str]

    @field_validator("chapter_id")
    @classmethod
    def _valid_chapter_id(cls, value: str) -> str:
        chapter_id = value.strip()
        if not chapter_id:
            raise ValueError("章节不能为空")
        return chapter_id

    @field_validator("utterance_ids")
    @classmethod
    def _valid_utterance_ids(cls, value: list[str]) -> list[str]:
        utterance_ids = [item.strip() for item in value if item.strip()]
        if not utterance_ids:
            raise ValueError("至少选择一条讲述记录")
        return list(dict.fromkeys(utterance_ids))


class MemoirBookChapterDraftVersionResponse(BaseModel):
    id: str
    draft_id: str
    project_id: str
    chapter_id: str
    version: int
    content: str
    created_by: MemoirCreatedBy
    model: str | None = None
    prompt_snapshot: str
    feedback: str | None = None
    created_at: datetime


class MemoirBookChapterDraftRevisionRequest(BaseModel):
    expected_version_id: str = Field(min_length=1, max_length=36)
    content: str = Field(min_length=1, max_length=200_000)

    @field_validator("expected_version_id", "content")
    @classmethod
    def _strip_draft_revision(cls, value: str) -> str:
        return value.strip()


class MemoirBookChapterDraftResponse(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    status: MemoirBookChapterDraftStatus
    metadata_json: dict[str, object] = Field(default_factory=dict)
    pending_source_count: int = 0
    removed_source_count: int = 0
    latest_version: MemoirBookChapterDraftVersionResponse | None = None
    versions: list[MemoirBookChapterDraftVersionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirBookAssemblyResponse(BaseModel):
    project_id: str
    output_id: str
    output_version_id: str
    version: int
    content: str
    chapter_snapshot_json: list[dict[str, object]]
    created_at: datetime


class MemoirBookAudioRequest(BaseModel):
    voice: str | None = None
    speed_ratio: float | None = None
    force_regenerate: bool = False

    @field_validator("voice")
    @classmethod
    def _valid_voice(cls, value: str | None) -> str | None:
        if value is None:
            return None
        voice = value.strip()
        return voice or None

    @field_validator("speed_ratio")
    @classmethod
    def _valid_speed_ratio(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return max(0.5, min(2.0, round(float(value), 2)))


class MemoirBookAudioResponse(BaseModel):
    audio_file_ids: list[str]
    audio_file_durations_seconds: list[float | None] = Field(default_factory=list)
    provider: str
    model: str
    mime_type: str
    chunk_count: int
    source_text_hash: str
    duration_seconds: float | None = None
    alignment_provider: str | None = None
    alignment: list[MemoirTTSAlignmentSegmentResponse] = Field(default_factory=list)


MemoirBookCoverStyle = Literal["classic_plain", "warm_gray", "dark_album", "moonlit_courtyard", "navy_gilt"]


class MemoirBookOutputUpdateRequest(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    narrator_name: str | None = None
    prepared_by: str | None = None
    preface: str | None = None
    afterword: str | None = None
    back_cover_quote: str | None = None
    cover_style: MemoirBookCoverStyle | None = None

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            raise ValueError("书名不能为空")
        return title

    @field_validator("subtitle", "narrator_name", "prepared_by", "preface", "afterword", "back_cover_quote")
    @classmethod
    def _valid_optional_book_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


MemoirBookEditorialCopyTarget = Literal[
    "preface",
    "afterword",
    "back_cover_quote",
    "chapter_intros",
    "pull_quotes",
]


class MemoirBookOutputEditorialCopyRegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_model: str | None = None
    targets: list[MemoirBookEditorialCopyTarget] = Field(default_factory=list)
    chapter_ids: list[str] = Field(default_factory=list)
    feedback: str | None = None

    @field_validator("ai_model", "feedback")
    @classmethod
    def _valid_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("chapter_ids")
    @classmethod
    def _valid_chapter_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("targets")
    @classmethod
    def _valid_targets(cls, value: list[MemoirBookEditorialCopyTarget]) -> list[MemoirBookEditorialCopyTarget]:
        return list(dict.fromkeys(value))


class MemoirBookOutputChapterEditorialCopyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intro: str | None = None
    pull_quote: str | None = None
    pull_quote_source_ref_id: str | None = None

    @field_validator("intro", "pull_quote", "pull_quote_source_ref_id")
    @classmethod
    def _valid_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class MemoirBookTitleSuggestionRequest(BaseModel):
    ai_model: str | None = None
    count: int = 6

    @field_validator("ai_model")
    @classmethod
    def _valid_ai_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        model = value.strip()
        return model or None

    @field_validator("count")
    @classmethod
    def _valid_count(cls, value: int) -> int:
        return max(3, min(8, int(value)))


class MemoirBookTitleSuggestionResponse(BaseModel):
    suggestions: list[str]
    provider: str | None = None
    model: str | None = None


class MemoirBookOutputTableOfContentsItemResponse(BaseModel):
    chapter_id: str
    index: int
    title: str
    char_count: int = 0
    source_count: int = 0


class MemoirBookOutputStructuredChapterResponse(BaseModel):
    chapter_id: str
    index: int
    title: str
    content: str
    char_count: int = 0
    source_count: int = 0
    draft_version_id: str | None = None
    draft_version: int | None = None


class MemoirBookOutputStructuredContentResponse(BaseModel):
    title: str
    chapter_count: int = 0
    char_count: int = 0
    table_of_contents: list[MemoirBookOutputTableOfContentsItemResponse] = Field(default_factory=list)
    chapters: list[MemoirBookOutputStructuredChapterResponse] = Field(default_factory=list)


class MemoirBookManuscriptProfile(BaseModel):
    template: Literal["classic_family", "photo_memory", "oral_archive"] = "classic_family"
    title: str
    subtitle: str = ""
    narrator_name: str = ""
    prepared_by: str = "家人整理"
    interview_date_range: str = ""
    privacy_note: str = "本书仅供家庭内部保存与阅读。未经本人或家属同意，不得公开传播。"
    ai_disclosure_note: str = (
        "本书根据口述访谈整理而成。AI 的作用包括章节归类、语句通顺、目录梳理和标题生成，"
        "不主动新增未经口述确认的事实。"
    )


class MemoirBookManuscriptSection(BaseModel):
    kind: str
    title: str
    content: str = ""
    enabled: bool = True


class MemoirBookManuscriptQuote(BaseModel):
    text: str
    source_ref_ids: list[str] = Field(default_factory=list)
    source: Literal["original_quote", "editorial_generated", "manual"] = "original_quote"


class MemoirBookManuscriptPersonNote(BaseModel):
    name: str
    note: str = ""


class MemoirBookManuscriptUnconfirmedFact(BaseModel):
    text: str
    source_ref_ids: list[str] = Field(default_factory=list)


class MemoirBookManuscriptSourceRef(BaseModel):
    utterance_id: str = ""
    transcript_version_id: str | None = None
    voice_session_id: str | None = None
    quote: str = ""


class MemoirBookManuscriptIllustration(BaseModel):
    image_asset_id: str
    storage_path: str
    mime_type: str = ""
    caption: str = ""
    alt_text: str = ""
    placement: Literal["chapter_opening", "chapter_inline", "appendix", "cover", "back_cover"] = "chapter_opening"
    display_size: str = "full_width"
    source_quote: str = ""
    disclosure: str = "AI 根据讲述内容生成的意象插图，不是真实照片。"


class MemoirBookManuscriptChapter(BaseModel):
    chapter_id: str
    index: int
    title: str
    subtitle: str = ""
    intro: str = ""
    time_hint: str = ""
    place_hint: str = ""
    pull_quotes: list[MemoirBookManuscriptQuote] = Field(default_factory=list)
    people_notes: list[MemoirBookManuscriptPersonNote] = Field(default_factory=list)
    uncertain_facts: list[MemoirBookManuscriptUnconfirmedFact] = Field(default_factory=list)
    content: str
    source_refs: list[MemoirBookManuscriptSourceRef] = Field(default_factory=list)
    illustrations: list[MemoirBookManuscriptIllustration] = Field(default_factory=list)


class MemoirBookManuscriptRenderSettings(BaseModel):
    page_size: Literal["a5"] = "a5"
    layout: Literal["reading", "print"] = "reading"
    font_scale: Literal["standard", "large"] = "standard"
    show_headers: bool = True
    show_source_appendix: bool = True
    photo_mode: Literal["none", "chapter", "appendix"] = "none"
    cover_style: MemoirBookCoverStyle = "classic_plain"
    back_cover_quote: str = ""
    editorial_copy_model: str | None = None


class MemoirBookManuscriptBackMatter(BaseModel):
    afterword: MemoirBookManuscriptSection
    interview_records: MemoirBookManuscriptSection
    version_note: MemoirBookManuscriptSection
    unconfirmed_facts: MemoirBookManuscriptSection
    source_appendix: MemoirBookManuscriptSection


class MemoirBookManuscript(BaseModel):
    schema_version: int = 1
    profile: MemoirBookManuscriptProfile
    front_matter: list[MemoirBookManuscriptSection] = Field(default_factory=list)
    table_of_contents: list[MemoirBookOutputTableOfContentsItemResponse] = Field(default_factory=list)
    chapters: list[MemoirBookManuscriptChapter] = Field(default_factory=list)
    image_appendix: list[MemoirBookManuscriptIllustration] = Field(default_factory=list)
    back_matter: MemoirBookManuscriptBackMatter
    render_settings: MemoirBookManuscriptRenderSettings = Field(default_factory=MemoirBookManuscriptRenderSettings)


class MemoirBookOutputVersionResponse(BaseModel):
    id: str
    output_id: str
    project_id: str
    plan_id: str
    version: int
    content: str
    chapter_snapshot_json: list[dict[str, object]] = Field(default_factory=list)
    structured_content: MemoirBookOutputStructuredContentResponse | None = None
    manuscript: MemoirBookManuscript | None = None
    created_by: MemoirCreatedBy
    created_at: datetime


class MemoirBookOutputResponse(BaseModel):
    id: str
    project_id: str
    plan_id: str
    title: str
    status: MemoirOutputStatus
    formal_version_id: str | None = None
    formal_audit_job_id: str | None = None
    formalized_by_user_id: str | None = None
    formalized_at: datetime | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    is_stale: bool = False
    stale_chapter_titles: list[str] = Field(default_factory=list)
    latest_version: MemoirBookOutputVersionResponse | None = None
    versions: list[MemoirBookOutputVersionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
