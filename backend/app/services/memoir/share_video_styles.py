from __future__ import annotations

from typing import Literal

from app.config import get_settings
from app.services.ai_costs import estimate_image_cost
from app.services.memoir.share_video_scene_planner import SHARE_VIDEO_SCENE_MAX_COUNT


ShareVideoStyle = Literal["uploaded", "illustrated"]
DEFAULT_SHARE_VIDEO_STYLE: ShareVideoStyle = "illustrated"


def normalize_share_video_style(value: object) -> ShareVideoStyle:
    # Older projects and jobs have no style field and use the existing renderer.
    if value == "uploaded":
        return "uploaded"
    return DEFAULT_SHARE_VIDEO_STYLE


def share_video_style_options() -> list[dict[str, str]]:
    # Shown before making a video so the author knows what AI pictures cost.
    image_price = estimate_image_cost(provider="volc_ark", model=get_settings().volc_ark_image_model).amount_rmb
    price_note = (
        f"按张计费，约 {image_price:g} 元/张，一条最多 {SHARE_VIDEO_SCENE_MAX_COUNT} 张。" if image_price else ""
    )
    return [
        {
            "value": "uploaded",
            "label": "自己配图",
            "description": "免费。用自己的照片或图片素材里的图，配上讲述原声和字幕。",
        },
        {
            "value": "illustrated",
            "label": "AI配图",
            "description": f"{price_note}AI 根据讲述内容生成插图，配上讲述原声和字幕。",
        },
    ]
