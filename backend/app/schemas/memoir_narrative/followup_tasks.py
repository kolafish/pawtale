from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirFollowupTaskClaimRole,
    MemoirFollowupTaskStatus,
    MemoirFollowupTaskUtteranceRole,
    MemoirNarrativeActorType,
)
from app.domain.memoir.types import MemoirEditingJobStatus


class MemoirFollowupTaskClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: str
    context_role: MemoirFollowupTaskClaimRole


class MemoirFollowupTaskUtteranceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    utterance_id: str
    result_role: MemoirFollowupTaskUtteranceRole
    created_at: datetime


class MemoirFollowupTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    gap_id: str
    chapter_id: str | None
    story_id: str | None
    target_slot: str
    question: str
    purpose: str
    known_context_summary: str
    priority: int
    status: MemoirFollowupTaskStatus
    assigned_session_id: str | None
    defer_until: datetime | None
    defer_reason: str
    created_by: MemoirNarrativeActorType
    started_at: datetime | None
    completed_at: datetime | None
    claims: list[MemoirFollowupTaskClaimResponse] = Field(default_factory=list)
    utterances: list[MemoirFollowupTaskUtteranceResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirFollowupTaskListResponse(BaseModel):
    items: list[MemoirFollowupTaskResponse] = Field(default_factory=list)
    total: int = 0


class MemoirFollowupTaskGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str | None = Field(default=None, max_length=36)


class MemoirFollowupTaskGenerationJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str | None
    status: MemoirEditingJobStatus
    result_json: dict
    error_message: str
    created_at: datetime
    updated_at: datetime


class MemoirFollowupTaskActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=2000)

    @field_validator("idempotency_key", "note")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class MemoirFollowupTaskDeferRequest(MemoirFollowupTaskActionRequest):
    defer_until: datetime | None = None

    @model_validator(mode="after")
    def require_reason(self):
        if not self.note:
            raise ValueError("deferring a follow-up task requires a reason")
        return self


class MemoirFollowupTaskCannotRecallRequest(MemoirFollowupTaskActionRequest):
    resolution: Literal["accept_incomplete", "ask_family_later"]

    @model_validator(mode="after")
    def require_note(self):
        if not self.note:
            raise ValueError("cannot-recall handling requires a note")
        return self


class MemoirFollowupTaskSwitchRequest(MemoirFollowupTaskActionRequest):
    current_task_id: str = Field(min_length=1, max_length=36)

    @model_validator(mode="after")
    def require_reason(self):
        if not self.note:
            raise ValueError("switching a follow-up task requires a reason")
        return self
