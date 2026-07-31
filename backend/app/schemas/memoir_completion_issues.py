from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.domain.memoir.types import (
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssuePriority,
    MemoirCompletionIssueScope,
    MemoirCompletionIssueStatus,
    MemoirCompletionIssueType,
)


class MemoirCompletionIssueResponse(BaseModel):
    id: str
    project_id: str
    scope: MemoirCompletionIssueScope
    chapter_id: str | None = None
    issue_type: MemoirCompletionIssueType
    channel: MemoirCompletionIssueChannel
    question: str
    reason: str
    priority: MemoirCompletionIssuePriority
    related_material_ids: list[str] = Field(default_factory=list)
    input_signature: str
    status: MemoirCompletionIssueStatus
    active_session_id: str | None = None
    outcome: MemoirCompletionIssueOutcome | None = None
    outcome_note: str
    answered_material_id: str | None = None
    started_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoirCompletionIssueCollectionResponse(BaseModel):
    project_id: str
    issues: list[MemoirCompletionIssueResponse] = Field(default_factory=list)


class MemoirCompletionIssueCloseRequest(BaseModel):
    outcome: MemoirCompletionIssueOutcome
    note: str = Field(default="", max_length=1000)
    answered_material_id: str | None = None

    @model_validator(mode="after")
    def validate_answer_material(self):
        if (
            self.outcome == MemoirCompletionIssueOutcome.ANSWERED
            and not self.answered_material_id
        ):
            raise ValueError("answered outcome requires answered_material_id")
        return self
