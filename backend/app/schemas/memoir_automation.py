from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
)


class MemoirAutomationEventResponse(BaseModel):
    id: str
    project_id: str
    event_type: MemoirAutomationEventType
    status: MemoirAutomationEventStatus
    chapter_id: str | None = None
    editing_job_id: str | None = None
    utterance_ids: list[str] = Field(default_factory=list)
    summary_text: str
    confidence: float | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class MemoirAutomationEventListResponse(BaseModel):
    items: list[MemoirAutomationEventResponse] = Field(default_factory=list)
    total: int
    limit: int


class MemoirAutomationEventAcknowledgeDismissibleResponse(BaseModel):
    updated_count: int


class MemoirAutomationEventVoicePlaybackRequest(BaseModel):
    event_ids: list[str] = Field(default_factory=list, max_length=20)
    voice_session_id: str
    message_id: str


class MemoirAutomationEventVoicePlaybackResponse(BaseModel):
    updated_count: int
