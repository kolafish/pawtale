from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.memoir.types import MemoirEditingJobStatus


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
    "start_interview",
    "confirm",
    "review_draft",
    "complete",
]


class MemoirChapterPreparationJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_id: str
    status: MemoirEditingJobStatus
    created_at: datetime


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
    active_session_id: str | None = None


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
    project_id: str
    chapter_id: str
    chapter_title: str
    completion_summary: MemoirChapterCompletionSummary
    draft_id: str | None = None
    draft_version_id: str | None = None
    draft_version: int | None = None
    draft_status: str | None = None
    draft_content: str = ""
