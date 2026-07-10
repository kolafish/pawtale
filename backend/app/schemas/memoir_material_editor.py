from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.memoir_voice import MemoirVoiceTranscriptItemResponse


class MemoirMaterialItemResponse(MemoirVoiceTranscriptItemResponse):
    has_manual_correction: bool
    manual_corrected_at: datetime | None = None


class MemoirMaterialListResponse(BaseModel):
    items: list[MemoirMaterialItemResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
