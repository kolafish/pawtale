from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirCompletionItemStatus,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
)


class MemoirCompletionItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    scope_type: MemoirNarrativeGapScopeType
    chapter_id: str | None
    story_id: str | None
    claim_id: str | None
    related_claim_id: str | None
    item_type: MemoirNarrativeGapType
    severity: MemoirNarrativeGapSeverity
    dimension: str
    title: str
    description: str
    action: MemoirNarrativeGapRecommendedAction
    question: str
    priority: int
    status: MemoirCompletionItemStatus
    defer_until: datetime | None
    outcome_note: str
    detected_by: MemoirNarrativeGapDetectedBy
    detection_count: int
    first_detected_at: datetime
    last_detected_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoirCompletionItemActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=2000)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str) -> str:
        return value.strip()


class MemoirCompletionItemDeferRequest(MemoirCompletionItemActionRequest):
    defer_until: datetime | None = None

    @model_validator(mode="after")
    def require_note(self):
        if not self.note:
            raise ValueError("暂时跳过时请说明原因")
        return self


class MemoirCompletionItemSwitchRequest(MemoirCompletionItemActionRequest):
    current_item_id: str = Field(min_length=1, max_length=36)


class MemoirCompletionOutcomeResponse(BaseModel):
    status: Literal["processing", "incorporated", "not_used"]
    voice_session_id: str
    item_id: str | None = None
    item_status: MemoirCompletionItemStatus | None = None
    chapter_id: str | None = None
    chapter_title: str = ""
    utterance_id: str | None = None
    message: str
