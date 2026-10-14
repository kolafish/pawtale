from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirImageAssetStatus
from app.models.memoir import MemoirImageAsset
from app.services.memoir.image_assets import image_asset_file_path


SHARE_VIDEO_BACKGROUND_SOURCE_AI = "ai"
SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM = "custom"
SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC = "classic"
DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE = SHARE_VIDEO_BACKGROUND_SOURCE_AI
_BACKGROUND_SOURCE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": SHARE_VIDEO_BACKGROUND_SOURCE_AI,
        "label": "AI生成",
        "description": "根据文字自动生成回忆录背景图，可选择单图或智能多图",
    },
    {
        "value": SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM,
        "label": "自定义图片",
        "description": "上传自己的图片，自动处理成适合叠加文字的视频背景",
    },
    {
        "value": SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC,
        "label": "经典背景",
        "description": "使用素雅的暖色底，不生成也不上传图片",
    },
)
_BACKGROUND_SOURCE_VALUES = {option["value"] for option in _BACKGROUND_SOURCE_OPTIONS}
# Each photo stays on screen long enough for its camera move and crossfade.
SHARE_VIDEO_PHOTO_MIN_MS = 3000
# The most photos WeChat lets the user pick from the album at once.
SHARE_VIDEO_PHOTO_MAX_COUNT = 20


def max_share_video_photo_count(duration_ms: int) -> int:
    return max(1, min(SHARE_VIDEO_PHOTO_MAX_COUNT, duration_ms // SHARE_VIDEO_PHOTO_MIN_MS))


def share_video_background_source_options(*, include_ai: bool = True) -> list[dict[str, str]]:
    return [
        dict(option)
        for option in _BACKGROUND_SOURCE_OPTIONS
        if include_ai or option["value"] != SHARE_VIDEO_BACKGROUND_SOURCE_AI
    ]


def normalize_share_video_background_source(value: str | None) -> str:
    normalized = (value or DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE).strip()
    if normalized not in _BACKGROUND_SOURCE_VALUES:
        valid = "、".join(option["label"] for option in _BACKGROUND_SOURCE_OPTIONS)
        raise ValueError(f"未知的分享视频背景来源，可选：{valid}")
    return normalized


async def load_share_video_photo_paths(
    db: AsyncSession,
    *,
    project_id: str,
    asset_ids: list[str],
) -> list[Path] | None:
    """Files of the chosen image-library pictures in order, or None if one is gone."""
    assets = {
        asset.id: asset
        for asset in await db.scalars(
            select(MemoirImageAsset).where(
                MemoirImageAsset.project_id == project_id,
                MemoirImageAsset.id.in_(asset_ids),
                MemoirImageAsset.status == MemoirImageAssetStatus.READY.value,
            )
        )
    }
    if any(asset_id not in assets for asset_id in asset_ids):
        return None
    return [image_asset_file_path(assets[asset_id]) for asset_id in asset_ids]
