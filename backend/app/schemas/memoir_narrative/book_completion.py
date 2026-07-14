from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


MemoirBookCompletionState = Literal[
    "ready",
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
    "open_book",
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
