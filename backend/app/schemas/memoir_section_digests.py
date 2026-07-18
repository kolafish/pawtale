from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

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
