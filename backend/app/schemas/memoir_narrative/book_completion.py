from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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


class MemoirBookCompletionResponse(BaseModel):
    project_id: str
    state: MemoirBookCompletionState
    action: MemoirBookCompletionAction
    headline: str
    detail: str
    task_id: str | None = None
    chapter_id: str | None = None


class MemoirBookCompletionChapterResponse(BaseModel):
    chapter_id: str
    title: str
    sort_order: int
    state: MemoirBookCompletionState
    headline: str
    detail: str
    focus_areas: list[str] = Field(default_factory=list)
    task_id: str | None = None
    active_session_id: str | None = None


class MemoirBookCompletionOverviewResponse(BaseModel):
    project_id: str
    headline: str
    detail: str
    total_chapters: int
    completed_chapters: int
    chapters: list[MemoirBookCompletionChapterResponse]
