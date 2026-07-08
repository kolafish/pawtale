from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.memoir.types import (
    MemoirChatMessageRole,
    MemoirGuidanceMode,
    MemoirInterviewType,
    MemoirVoiceSessionStatus,
)
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
