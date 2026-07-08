from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.memoir.types import MemoirBookPlanStatus
from app.schemas.memoir_book import (
    MemoirBookPlanChapterResponse,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceIncludedChapter,
    MemoirBookSourceMoveRequest,
    MemoirBookSourceRemoveRequest,
)


class MemoirChapterSourceEditorSourceResponse(BaseModel):
    source_index: int
    voice_session_id: str
    message_id: str
    utterance_id: str
    transcript_version_id: str | None = None
    audio_file_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    text: str
    created_at: datetime
    source_material_type: str = ""
    source_route_action: str = ""
    source_route_confidence: float | None = None
    source_route_reason: str = ""
    source_route_updated_at: datetime | None = None
    is_included: bool = False
    included_in_chapters: list[MemoirBookSourceIncludedChapter] = Field(default_factory=list)
    current_chapter_id: str | None = None
    current_chapter_title: str | None = None
    current_chapter_sort_order: int | None = None


class MemoirChapterSourceEditorResponse(BaseModel):
    project_id: str
    plan_id: str | None = None
    plan_title: str | None = None
    plan_status: MemoirBookPlanStatus | None = None
    total_source_count: int
    covered_source_count: int
    uncovered_source_count: int
    chapters: list[MemoirBookPlanChapterResponse] = Field(default_factory=list)
    sources: list[MemoirChapterSourceEditorSourceResponse] = Field(default_factory=list)
    uncovered_sources: list[MemoirChapterSourceEditorSourceResponse] = Field(default_factory=list)


MemoirChapterSourceEditorAssignmentRequest = MemoirBookSourceAssignmentRequest
MemoirChapterSourceEditorMoveRequest = MemoirBookSourceMoveRequest
MemoirChapterSourceEditorRemoveRequest = MemoirBookSourceRemoveRequest
