from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.domain.memoir.types import (
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirGuidanceMode,
    MemoirInterviewType,
    MemoirVoiceSessionStatus,
)
from app.schemas.memoir_voice import MemoirVoiceTranscriptItemResponse
from app.schemas.memoir_book import MemoirBookSourceIncludedChapter


class MemoirInterviewEditorAnswerResponse(BaseModel):
    id: str
    message_id: str
    parent_question_message_id: str | None = None
    voice_session_id: str
    project_id: str
    audio_file_id: str | None = None
    audio_mime_type: str | None = None
    audio_size_bytes: int | None = None
    audio_duration_seconds: int | None = None
    transcript_version_id: str | None = None
    transcript_raw: str
    transcript_edited: str
    display_text: str
    start_ms: int | None = None
    end_ms: int | None = None
    provider: str | None = None
    source_material_type: str = ""
    source_route_action: str = ""
    source_route_confidence: float | None = None
    source_route_reason: str = ""
    source_route_updated_at: datetime | None = None
    is_included_in_book: bool = False
    included_chapters: list[MemoirBookSourceIncludedChapter] = Field(default_factory=list)
    turn_index: int
    created_at: datetime


class MemoirInterviewEditorQuestionResponse(BaseModel):
    id: str
    voice_session_id: str
    project_id: str
    role: MemoirChatMessageRole
    text: str
    audio_file_id: str | None = None
    transcript_version_id: str | None = None
    utterance_id: str | None = None
    answer_count: int
    answers: list[MemoirInterviewEditorAnswerResponse] = Field(default_factory=list)
    turn_index: int
    created_at: datetime
    updated_at: datetime


class MemoirInterviewEditorTimelineItemResponse(BaseModel):
    message_id: str
    voice_session_id: str
    project_id: str
    role: MemoirChatMessageRole
    parent_message_id: str | None = None
    text: str
    audio_file_id: str | None = None
    transcript_version_id: str | None = None
    utterance_ids: list[str] = Field(default_factory=list)
    source_material_type: str = ""
    source_route_action: str = ""
    turn_index: int
    created_at: datetime
    updated_at: datetime


class MemoirInterviewEditorSessionSummaryResponse(BaseModel):
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
    question_count: int
    answer_count: int
    unassigned_answer_count: int
    total_audio_seconds: int
    summary_text: str
    last_message_text: str
    last_message_role: MemoirChatMessageRole | None = None
    last_message_at: datetime | None = None


class MemoirInterviewEditorSessionListResponse(BaseModel):
    items: list[MemoirInterviewEditorSessionSummaryResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class MemoirInterviewEditorSessionDetailResponse(BaseModel):
    session: MemoirInterviewEditorSessionSummaryResponse
    questions: list[MemoirInterviewEditorQuestionResponse] = Field(default_factory=list)
    unassigned_answers: list[MemoirInterviewEditorAnswerResponse] = Field(default_factory=list)
    raw_timeline: list[MemoirInterviewEditorTimelineItemResponse] = Field(default_factory=list)


class MemoirInterviewEditorReviewIssue(BaseModel):
    code: str
    severity: str
    message: str
    count: int = 0


class MemoirInterviewEditorSessionReviewResponse(BaseModel):
    project_id: str
    voice_session_id: str
    project_title: str
    generated_at: datetime
    question_count: int
    answer_count: int
    unassigned_answer_count: int
    missing_audio_count: int
    not_included_answer_count: int
    issues: list[MemoirInterviewEditorReviewIssue] = Field(default_factory=list)
    markdown: str


class MemoirInterviewEditorQuestionCreateRequest(BaseModel):
    voice_session_id: str
    text: str

    @field_validator("voice_session_id", "text")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized


class MemoirInterviewEditorQuestionTextUpdateRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def _valid_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("问题文字不能为空")
        return normalized


class MemoirInterviewEditorAnswerParentUpdateRequest(BaseModel):
    parent_question_message_id: str | None = None
    reason: str | None = None

    @field_validator("parent_question_message_id", "reason")
    @classmethod
    def _optional_trimmed_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MemoirDialectGlossaryEntry(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=160)

    @field_validator("source", "target")
    @classmethod
    def _required_trimmed_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("note")
    @classmethod
    def _optional_trimmed_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MemoirDialectGlossaryResponse(BaseModel):
    project_id: str
    entries: list[MemoirDialectGlossaryEntry] = Field(default_factory=list)


class MemoirDialectGlossaryUpdateRequest(BaseModel):
    entries: list[MemoirDialectGlossaryEntry] = Field(default_factory=list, max_length=80)


class MemoirBatchTranscriptCleanupPreviewRequest(BaseModel):
    utterance_ids: list[str] = Field(min_length=1, max_length=30)
    ai_model: str | None = None

    @field_validator("utterance_ids")
    @classmethod
    def _unique_utterance_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("至少选择一段回答")
        deduped = list(dict.fromkeys(normalized))
        if len(deduped) != len(normalized):
            raise ValueError("不能重复选择同一段回答")
        return deduped

    @field_validator("ai_model")
    @classmethod
    def _optional_trimmed_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MemoirBatchTranscriptCleanupPreviewItem(BaseModel):
    utterance_id: str
    source_text: str
    cleaned_text: str
    changed: bool
    provider: str | None = None
    created_by: MemoirCreatedBy


class MemoirBatchTranscriptCleanupPreviewResponse(BaseModel):
    project_id: str
    items: list[MemoirBatchTranscriptCleanupPreviewItem] = Field(default_factory=list)
    glossary_entries: list[MemoirDialectGlossaryEntry] = Field(default_factory=list)


class MemoirBatchTranscriptCleanupConfirmItem(BaseModel):
    utterance_id: str
    cleaned_text: str = Field(min_length=1, max_length=20000)
    provider: str | None = Field(default=None, max_length=80)
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI

    @field_validator("utterance_id", "cleaned_text")
    @classmethod
    def _required_trimmed_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("provider")
    @classmethod
    def _optional_trimmed_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("created_by")
    @classmethod
    def _valid_cleanup_creator(cls, value: MemoirCreatedBy) -> MemoirCreatedBy:
        if value == MemoirCreatedBy.ASR:
            raise ValueError("批量清理结果不能标记为 ASR 原始版本")
        return value


class MemoirBatchTranscriptCleanupConfirmRequest(BaseModel):
    items: list[MemoirBatchTranscriptCleanupConfirmItem] = Field(min_length=1, max_length=30)
    ai_model: str | None = None

    @field_validator("items")
    @classmethod
    def _unique_items(
        cls,
        value: list[MemoirBatchTranscriptCleanupConfirmItem],
    ) -> list[MemoirBatchTranscriptCleanupConfirmItem]:
        ids = [item.utterance_id for item in value]
        if len(set(ids)) != len(ids):
            raise ValueError("不能重复选择同一段回答")
        return value

    @field_validator("ai_model")
    @classmethod
    def _optional_trimmed_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MemoirBatchTranscriptCleanupConfirmResponse(BaseModel):
    project_id: str
    updated_count: int
    items: list[MemoirVoiceTranscriptItemResponse] = Field(default_factory=list)
