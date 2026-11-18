from __future__ import annotations

from typing import Literal

from app.services.ai_costs import estimate_image_cost
from app.services.ai_credits import ai_usage_charge
from app.services.memoir.share_video_scene_planner import SHARE_VIDEO_SCENE_MAX_COUNT


ShareVideoStyle = Literal["uploaded", "illustrated"]
DEFAULT_SHARE_VIDEO_STYLE: ShareVideoStyle = "illustrated"


def normalize_share_video_style(value: object) -> ShareVideoStyle:
    # Older projects and jobs have no style field and use the existing renderer.
    if value == "uploaded":
        return "uploaded"
    return DEFAULT_SHARE_VIDEO_STYLE


def image_unit_price_rmb(model: str) -> str | None:
    cost = estimate_image_cost(provider="volc_ark", model=model).amount_rmb
    return format(ai_usage_charge(cost).normalize(), "f") if cost is not None else None


def share_video_style_options(image_price: str | None) -> list[dict[str, str]]:
    price_note = (
        f"按张计费，约 {image_price} 元/张，最多 {SHARE_VIDEO_SCENE_MAX_COUNT} 张视频画面。多人故事如需统一人物形象，另计 1 张定稿图。" if image_price else ""
    )
    return [
        {
            "value": "uploaded",
            "label": "自己配图",
            "description": "用已有照片或插图，免费做视频。",
        },
        {
            "value": "illustrated",
            "label": "AI配图",
            "description": f"{price_note}AI 根据讲述内容生成插图，配上讲述原声和字幕。",
        },
    ]
