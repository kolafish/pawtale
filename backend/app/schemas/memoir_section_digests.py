from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.memoir.types import MemoirSemanticCacheStatus


class MemoirSectionDigestGenerateRequest(BaseModel):
    ai_model: str | None = None
    force_regenerate: bool = False


class MemoirSectionDigestResponse(BaseModel):
    id: str
    project_id: str
    section_id: str
    input_signature: str
    engine: str
    status: MemoirSemanticCacheStatus
    digest: dict
    error_message: str
    created_at: datetime
    updated_at: datetime


class MemoirBodyDigestPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    material_ids: list[str] = Field(default_factory=list)


class MemoirBodyDigest(BaseModel):
    """Observations about one saved body, never instructions to an editor."""
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1)
    focus: str = ""
    points: list[MemoirBodyDigestPoint] = Field(default_factory=list)
