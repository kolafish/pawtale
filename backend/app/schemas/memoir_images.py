from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.types import (
    MemoirBookImagePlacementType,
    MemoirEditingJobStatus,
    MemoirImageAssetSourceType,
    MemoirImageAssetStatus,
)


MemoirImageDisplaySize = Literal["full_width", "half_width", "inline"]


class MemoirMaterialImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    style_id: str | None = Field(default=None, max_length=80)

    @field_validator("style_id")
    @classmethod
    def _valid_style_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _valid_range(self) -> "MemoirMaterialImageGenerationRequest":
        if self.end_char <= self.start_char:
            raise ValueError("结束位置必须大于开始位置")
        return self


class MemoirMaterialImageGenerationJobResponse(BaseModel):
    job_id: str
    project_id: str
    utterance_id: str
    asset_id: str
    status: MemoirEditingJobStatus
    asset_status: MemoirImageAssetStatus
    stage: str = "queued"
    selected_text: str
    start_char: int
    end_char: int
    style_id: str
    error_message: str = ""
    created_at: datetime
    updated_at: datetime


class MemoirBookImagePlacementResponse(BaseModel):
    id: str
    project_id: str
    image_asset_id: str
    placement_type: MemoirBookImagePlacementType
    chapter_id: str | None = None
    chapter_title: str = ""
    caption_override: str = ""
    display_size: str = "full_width"
    sort_order: int = 0
    enabled: bool = True
    created_at: datetime
    updated_at: datetime


class MemoirImageAssetResponse(BaseModel):
    id: str
    project_id: str
    status: MemoirImageAssetStatus
    source_type: MemoirImageAssetSourceType
    source_signature: str
    storage_path: str
    thumbnail_storage_path: str = ""
    file_url: str
    thumbnail_url: str
    mime_type: str = ""
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    title: str = ""
    caption: str = ""
    alt_text: str = ""
    style_id: str = ""
    prompt_hash: str = ""
    prompt_version: str = ""
    provider: str = ""
    model: str = ""
    source_utterance_id: str | None = None
    source_chapter_id: str | None = None
    source_chapter_title: str = ""
    share_video_job_id: str | None = None
    source_quote: str = ""
    metadata_json: dict[str, object] = Field(default_factory=dict)
    include_in_book: bool = False
    placements: list[MemoirBookImagePlacementResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirImageAssetListResponse(BaseModel):
    items: list[MemoirImageAssetResponse]
    total: int
    ready_count: int
    included_count: int
    unused_count: int


class MemoirImageAssetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    caption: str | None = None
    alt_text: str | None = None

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()[:120]

    @field_validator("caption", "alt_text")
    @classmethod
    def _valid_long_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()[:500]


class MemoirBookImagePlacementCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    placement_type: MemoirBookImagePlacementType = MemoirBookImagePlacementType.CHAPTER_OPENING
    chapter_id: str | None = None
    caption_override: str | None = None
    display_size: MemoirImageDisplaySize = "full_width"

    @field_validator("chapter_id")
    @classmethod
    def _valid_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("caption_override")
    @classmethod
    def _valid_caption(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()[:500]


class MemoirBookImagePlacementUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption_override: str | None = None
    display_size: MemoirImageDisplaySize | None = None
    enabled: bool | None = None

    @field_validator("caption_override")
    @classmethod
    def _valid_caption(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()[:500]
