from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator
from app.schemas.memoir_sections import (
    MemoirChapterDraftSectionResponse,
    MemoirChapterSectionSummary,
)


MemoirBookCompletionState = Literal[
    "not_started",
    "preparing",
    "needs_input",
    "draft_ready",
    "confirmed",
    "failed",
]
MemoirChapterCompletionIssueStatus = Literal["pending", "in_progress"]
MemoirChapterCompletionNextActionKind = Literal[
    "prepare",
    "wait",
    "review_sections",
    "confirm",
    "review_draft",
    "complete",
]


class MemoirChapterAIRequest(BaseModel):
    ai_model: str | None = Field(default=None, max_length=120)


class MemoirChapterDraftSectionAIRevisionRequest(MemoirChapterAIRequest):
    expected_version_id: str = Field(min_length=1, max_length=36)
    instruction_text: str = Field(min_length=1, max_length=800)

    @field_validator("expected_version_id")
    @classmethod
    def _strip_expected_version_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("expected_version_id 不能为空")
        return normalized

    @field_validator("instruction_text")
    @classmethod
    def _strip_instruction_text(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("instruction_text 不能为空")
        return normalized


class MemoirChapterPreparationJobRequest(MemoirChapterAIRequest):
    operation: Literal["prepare", "rewrite"] = "prepare"


class MemoirChapterPreparationJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    chapter_id: str
    operation: Literal["prepare", "rewrite"] = "prepare"
    status: Literal[
        "idle",
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ] = "idle"
    stage: str = "idle"
    total_section_count: int = 0
    checked_section_count: int = 0
    completed_section_count: int = 0
    resumed_section_count: int = 0
    current_section_id: str | None = None
    current_section_title: str = ""
    outcome: str = ""
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    can_cancel: bool = False
    error_message: str = ""


class MemoirBookCompletionJobRequest(MemoirChapterAIRequest):
    retry_job_id: str | None = None


class MemoirBookCompletionJobChapterResponse(BaseModel):
    chapter_id: str
    title: str
    job_id: str | None = None
    status: Literal[
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ]
    error_message: str = ""


class MemoirBookCompletionJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    status: Literal[
        "idle",
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ] = "idle"
    total_chapter_count: int = 0
    processed_chapter_count: int = 0
    succeeded_chapter_count: int = 0
    failed_chapter_count: int = 0
    cancelled_chapter_count: int = 0
    current_chapter_id: str | None = None
    current_chapter_title: str = ""
    requested_ai_model: str | None = None
    chapters: list[MemoirBookCompletionJobChapterResponse] = Field(default_factory=list)
    can_cancel: bool = False
    can_retry: bool = False
    error_message: str = ""


class MemoirChapterCompletionNextAction(BaseModel):
    kind: MemoirChapterCompletionNextActionKind
    title: str
    detail: str
    action_text: str = ""
    disabled: bool = False
    issue_id: str | None = None


class MemoirChapterCompletionIssue(BaseModel):
    id: str
    question: str
    status: MemoirChapterCompletionIssueStatus


class MemoirChapterCompletionSummary(BaseModel):
    chapter_id: str
    title: str
    sort_order: int
    state: MemoirBookCompletionState
    next_action: MemoirChapterCompletionNextAction
    active_issue: MemoirChapterCompletionIssue | None = None
    headline: str
    detail: str


class MemoirBookCompletionOverviewResponse(BaseModel):
    project_id: str
    headline: str
    detail: str
    total_chapters: int
    completed_chapters: int
    chapters: list[MemoirChapterCompletionSummary]


class MemoirChapterWorkspaceResponse(BaseModel):
    server_revision: int = Field(ge=0)
    project_id: str
    chapter_id: str
    chapter_title: str
    completion_summary: MemoirChapterCompletionSummary
    draft_needs_rewrite: bool = False
    can_rewrite_draft: bool = False
    pending_assignment_material_count: int = 0
    pending_completion_source_count: int = 0
    pending_new_source_count: int = 0
    sections: list[MemoirChapterSectionSummary] = Field(default_factory=list)
    draft_id: str | None = None
    draft_version_id: str | None = None
    draft_version: int | None = None
    draft_status: str | None = None
    draft_content: str = ""
    draft_sections: list[MemoirChapterDraftSectionResponse] = Field(
        default_factory=list
    )
