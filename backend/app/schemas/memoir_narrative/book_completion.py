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
MemoirChapterCompletionItemStatus = Literal["pending", "in_progress", "deferred"]
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
    item_id: str | None = None


class MemoirChapterCompletionItem(BaseModel):
    id: str
    question: str
    status: MemoirChapterCompletionItemStatus
    active_session_id: str | None = None


class MemoirChapterCompletionSummary(BaseModel):
    chapter_id: str
    title: str
    sort_order: int
    state: MemoirBookCompletionState
    next_action: MemoirChapterCompletionNextAction
    active_item: MemoirChapterCompletionItem | None = None
    headline: str
    detail: str


class MemoirBookCompletionOverviewResponse(BaseModel):
    project_id: str
    headline: str
    detail: str
    total_chapters: int
    completed_chapters: int
    chapters: list[MemoirChapterCompletionSummary]
