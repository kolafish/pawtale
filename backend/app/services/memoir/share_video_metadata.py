from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.memoir.share_video_backgrounds import (
    DEFAULT_SHARE_VIDEO_BACKGROUND_MODE,
    DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE,
    SHARE_VIDEO_BACKGROUND_MODE_SINGLE,
)
from app.services.memoir.share_video_custom_backgrounds import DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE
from app.services.memoir.share_video_styles import DEFAULT_SHARE_VIDEO_STYLE, ShareVideoStyle
from app.services.memoir.share_video_story_plan import ShareVideoStoryPlan


BackgroundStatus = Literal[
    "pending",
    "ready",
    "classic",
    "disabled",
    "planning",
    "generating",
    "failed",
    "partial",
    "fallback",
]


class _ShareVideoMetadataModel(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class ShareVideoCharTiming(_ShareVideoMetadataModel):
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=0, ge=0)


class ShareVideoPlannedSceneMetadata(_ShareVideoMetadataModel):
    index: int = Field(default=0, ge=0)
    start_char: int = Field(default=0, ge=0)
    end_char: int = Field(default=0, ge=0)
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=0, ge=0)
    title: str = ""
    sentence_ids: list[int] = Field(default_factory=list)
    evidence_quote: str = ""


class ShareVideoScenePlanMetadata(_ShareVideoMetadataModel):
    version: str = ""
    provider: str = ""
    model: str = ""
    strategy: str = ""
    plan_hash: str = ""
    visual_context: str = ""
    scenes: list[ShareVideoPlannedSceneMetadata] = Field(default_factory=list)


class ShareVideoSceneAssetMetadata(ShareVideoPlannedSceneMetadata):
    background_status: Literal["ready", "failed", "disabled"] | None = None
    background_storage_path: str | None = None
    background_prompt_hash: str | None = None
    background_prompt_version: str | None = None
    background_provider: str | None = None
    background_model: str | None = None
    background_cache_hit: bool | None = None
    background_error_message: str = ""


class ShareVideoJobMetadata(_ShareVideoMetadataModel):
    pipeline_version: str = ""
    title: str = ""
    title_attempted: bool = False
    hook_text: str = ""
    alignment_strategy: str = ""
    alignment_confidence: float | None = None
    char_timings: list[ShareVideoCharTiming] = Field(default_factory=list)
    ai_model: str | None = None
    video_style: ShareVideoStyle = DEFAULT_SHARE_VIDEO_STYLE
    animation_version: str = ""
    animation_plan: ShareVideoStoryPlan | None = None
    background_source: str = DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE
    background_style: str = DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE
    background_mode: str = DEFAULT_SHARE_VIDEO_BACKGROUND_MODE
    background_effective_mode: str = SHARE_VIDEO_BACKGROUND_MODE_SINGLE
    custom_background_asset_id: str | None = None
    background_status: BackgroundStatus = "disabled"
    background_scene_count: int = Field(default=0, ge=0)
    background_generated_count: int = Field(default=0, ge=0)
    background_reused_scene_count: int = Field(default=0, ge=0)
    background_scene_plan: ShareVideoScenePlanMetadata | None = None
    background_scenes: list[ShareVideoSceneAssetMetadata] = Field(default_factory=list)
    background_plan_provider: str | None = None
    background_plan_model: str | None = None
    background_storage_path: str | None = None
    background_prompt_hash: str | None = None
    background_prompt_version: str | None = None
    background_provider: str | None = None
    background_model: str | None = None
    background_cache_hit: bool | None = None
    background_error_message: str = ""
    error_type: str = ""
    timeout_seconds: float | None = None
    elapsed_seconds: float | None = None

    @classmethod
    def from_json(cls, value: object) -> "ShareVideoJobMetadata":
        data = dict(value) if isinstance(value, dict) else {}
        if "video_style" not in data and data.get("background_source") == "custom":
            data["video_style"] = "uploaded"
        return cls.model_validate(data)

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)

    def char_timing_dicts(self) -> list[dict[str, int]]:
        return [timing.model_dump() for timing in self.char_timings]
