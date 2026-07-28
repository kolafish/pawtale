from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.domain.memoir.types import MemoirSemanticCacheStatus


class MemoirBookReviewGenerateRequest(BaseModel):
    ai_model: str | None = None
    force_regenerate: bool = False


class MemoirBookReviewAnswerApplyRequest(BaseModel):
    ai_model: str | None = None


class MemoirBookReviewResponse(BaseModel):
    id: str
    project_id: str
    input_signature: str
    engine: str
    status: MemoirSemanticCacheStatus
    review: dict
    error_message: str
    created_at: datetime
    updated_at: datetime


MemoirBookReviewJobStatus = Literal[
    "idle",
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]
MemoirBookReviewJobStage = Literal[
    "",
    "queued",
    "preparing_chapters",
    "reviewing",
    "saving",
    "completed",
    "failed",
    "cancelled",
]


class MemoirBookReviewJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    status: MemoirBookReviewJobStatus
    stage: MemoirBookReviewJobStage = ""
    total_chapter_count: int = 0
    processed_chapter_count: int = 0
    current_chapter_title: str = ""
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    review_id: str | None = None
    can_cancel: bool = False
    can_retry: bool = False
    error_message: str = ""


MemoirBookReviewAnswerApplicationStage = Literal[
    "",
    "queued",
    "assembling",
    "revising",
    "saving",
    "completed",
    "failed",
    "cancelled",
]


class MemoirBookReviewAnswerApplicationJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    status: MemoirBookReviewJobStatus
    stage: MemoirBookReviewAnswerApplicationStage = ""
    answer_count: int = 0
    unresolved_count: int = 0
    applied_answer_count: int = 0
    changed_chapter_count: int = 0
    summary: str = ""
    output_version_id: str | None = None
    output_version: int | None = None
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    already_applied: bool = False
    can_apply: bool = False
    can_cancel: bool = False
    can_retry: bool = False
    error_message: str = ""
