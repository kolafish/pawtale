from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.memoir.narrative_types import MemoirNarrativeGapRecommendedAction
from app.domain.memoir.types import MemoirEditingJobStatus


MemoirBookCompletionState = Literal[
    "ready",
    "in_progress",
    "preparing",
    "needs_preparation",
    "needs_review",
    "complete",
    "failed",
]
MemoirBookCompletionAction = Literal[
    "start_interview",
    "wait",
    "prepare",
    "open_chapter",
    "retry",
    "none",
]
MemoirChapterCompletionAuditStatus = Literal["not_started"] | MemoirEditingJobStatus
MemoirChapterCompletionTaskStatus = Literal["pending", "in_progress", "deferred"]
MemoirChapterCompletionDraftReadiness = Literal[
    "blocked",
    "ready_to_generate",
    "needs_review",
    "ready",
]
MemoirChapterCompletionNextActionKind = Literal[
    "create_direction",
    "confirm_direction",
    "refresh_audit",
    "wait",
    "continue_confirmation",
    "start_interview",
    "open_editorial_fix",
    "open_manual_review",
    "generate_draft",
    "review_draft",
    "complete",
]


class MemoirChapterCompletionNextAction(BaseModel):
    kind: MemoirChapterCompletionNextActionKind
    eyebrow: str
    title: str
    detail: str
    action_text: str = ""
    disabled: bool = False
    task_id: str | None = None


class MemoirChapterCompletionTask(BaseModel):
    id: str
    gap_id: str
    question: str
    purpose: str
    known_context_summary: str
    priority: int
    status: MemoirChapterCompletionTaskStatus
    active_session_id: str | None = None


class MemoirChapterCompletionGap(BaseModel):
    id: str
    title: str
    description: str
    recommended_action: MemoirNarrativeGapRecommendedAction


class MemoirChapterCompletionSummary(BaseModel):
    chapter_id: str
    title: str
    sort_order: int
    state: MemoirBookCompletionState
    audit_status: MemoirChapterCompletionAuditStatus
    audit_is_current: bool
    next_action: MemoirChapterCompletionNextAction
    must_handle_count: int
    suggested_count: int
    accepted_incomplete_count: int
    active_task: MemoirChapterCompletionTask | None = None
    visible_tasks: list[MemoirChapterCompletionTask] = Field(default_factory=list)
    editorial_gaps: list[MemoirChapterCompletionGap] = Field(default_factory=list)
    review_gaps: list[MemoirChapterCompletionGap] = Field(default_factory=list)
    draft_readiness: MemoirChapterCompletionDraftReadiness
    headline: str
    detail: str


class MemoirBookCompletionResponse(BaseModel):
    project_id: str
    state: MemoirBookCompletionState
    action: MemoirBookCompletionAction
    headline: str
    detail: str
    task_id: str | None = None
    chapter_id: str | None = None


class MemoirBookCompletionOverviewResponse(BaseModel):
    project_id: str
    headline: str
    detail: str
    total_chapters: int
    completed_chapters: int
    chapters: list[MemoirChapterCompletionSummary]
