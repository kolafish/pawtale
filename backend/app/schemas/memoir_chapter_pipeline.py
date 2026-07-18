from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirSemanticCacheStatus,
)


class MemoirChapterDigestGenerateRequest(BaseModel):
    ai_model: str | None = None
    force_regenerate: bool = False


class MemoirChapterDigestResponse(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    input_signature: str
    engine: str
    status: MemoirSemanticCacheStatus
    digest: dict
    error_message: str
    created_at: datetime
    updated_at: datetime


class MemoirLayeredChapterDraftGenerateRequest(BaseModel):
    ai_model: str | None = None
    editorial_guidance: list[str] = Field(default_factory=list, max_length=8)


class MemoirLayeredChapterDraftVersionResponse(BaseModel):
    id: str
    draft_id: str
    project_id: str
    chapter_id: str
    version: int
    content: str
    input_signature: str
    used_material_ids: list[str] = Field(default_factory=list)
    material_snapshot: list[dict] = Field(default_factory=list)
    created_by: str
    model: str | None = None
    created_at: datetime


class MemoirLayeredChapterDraftResponse(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    status: MemoirBookChapterDraftStatus
    latest_version: MemoirLayeredChapterDraftVersionResponse
    created_at: datetime
    updated_at: datetime
