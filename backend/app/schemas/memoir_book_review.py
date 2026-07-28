from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.domain.memoir.types import MemoirSemanticCacheStatus


class MemoirBookReviewGenerateRequest(BaseModel):
    ai_model: str | None = None
    force_regenerate: bool = False


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


class MemoirBookReviewJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    status: MemoirBookReviewJobStatus
    stage: str = ""
    total_chapter_count: int = 0
    processed_chapter_count: int = 0
    current_chapter_title: str = ""
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    review_id: str | None = None
    can_cancel: bool = False
    can_retry: bool = False
    error_message: str = ""
