from __future__ import annotations

from pydantic import BaseModel

from app.domain.memoir.narrative_types import (
    MemoirCompletionItemAction,
    MemoirCompletionItemKind,
    MemoirCompletionItemStatus,
)


class MemoirCompletionItem(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    story_id: str | None = None
    gap_id: str
    task_id: str | None = None
    kind: MemoirCompletionItemKind
    status: MemoirCompletionItemStatus
    action: MemoirCompletionItemAction
    gap_type: str
    title: str
    detail: str
    question: str = ""
    priority: int = 0
    active_session_id: str | None = None
    requires_manual_review: bool = False

