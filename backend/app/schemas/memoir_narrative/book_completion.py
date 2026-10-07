from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from app.schemas.memoir_book_review import (
    MemoirBookReviewJobResponse,
    MemoirBookReviewResponse,
    MemoirBookRevisionJobResponse,
)
from app.schemas.memoir_completion_issues import MemoirCompletionIssueResponse
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


class MemoirChapterSectionRevisionJobRequest(MemoirChapterAIRequest):
    operation: Literal["plan", "feedback", "draft"] = "plan"
    proposal_id: str | None = Field(default=None, max_length=36)
    feedback: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _validate_operation_input(self):
        self.proposal_id = (self.proposal_id or "").strip() or None
        self.feedback = (self.feedback or "").strip() or None
        if self.operation in {"plan", "feedback"} and not self.feedback:
            raise ValueError("请先输入或说出修改意见")
        if self.operation in {"feedback", "draft"} and not self.proposal_id:
            raise ValueError("缺少要继续处理的修改方案")
        return self


class MemoirChapterSectionRevisionJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    operation: Literal["plan", "feedback", "draft"] = "plan"
    status: Literal[
        "idle",
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ] = "idle"
    stage: str = ""
    proposal_id: str | None = None
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    error_message: str = ""


class MemoirChapterPreparationJobRequest(MemoirChapterAIRequest):
    operation: Literal["prepare", "rewrite"] = "prepare"


class MemoirChapterCandidateSection(BaseModel):
    section_id: str
    title: str
    content: str


class MemoirChapterCandidateDiffSegment(BaseModel):
    kind: Literal["equal", "deleted", "modified", "added"]
    text: str


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
    candidate_content: str | None = None
    candidate_base_version_id: str | None = None
    candidate_sections: list[MemoirChapterCandidateSection] = Field(
        default_factory=list
    )
    candidate_diff_segments: list[MemoirChapterCandidateDiffSegment] = Field(
        default_factory=list
    )
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
    blocking_issue_count: int = Field(default=0, ge=0)
    headline: str
    detail: str


class MemoirBookCompletionOverviewResponse(BaseModel):
    project_id: str
    headline: str
    detail: str
    total_chapters: int
    completed_chapters: int
    chapters: list[MemoirChapterCompletionSummary]
    blocking_issue_count: int = Field(default=0, ge=0)
    completion_job: MemoirBookCompletionJobResponse | None = None
    book_review: MemoirBookReviewResponse | None = None
    book_review_job: MemoirBookReviewJobResponse | None = None
    book_issues: list[MemoirCompletionIssueResponse] = Field(default_factory=list)
    book_output_available: bool = False
    revision_job: MemoirBookRevisionJobResponse | None = None


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
