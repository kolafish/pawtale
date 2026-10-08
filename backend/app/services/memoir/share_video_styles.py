from __future__ import annotations

from typing import Literal


ShareVideoStyle = Literal["uploaded", "illustrated"]
DEFAULT_SHARE_VIDEO_STYLE: ShareVideoStyle = "illustrated"


def normalize_share_video_style(value: object) -> ShareVideoStyle:
    # Older projects and jobs have no style field and use the existing renderer.
    if value == "uploaded":
        return "uploaded"
    return DEFAULT_SHARE_VIDEO_STYLE


def share_video_style_options() -> list[dict[str, str]]:
    return [
        {
            "value": "uploaded",
            "label": "自选画面",
            "description": "使用自己上传的照片或插图，配上原声与字幕。",
        },
        {
            "value": "illustrated",
            "label": "回忆画卷",
            "description": "AI 插图与字幕随原声展开，呈现讲述中的场景。",
        },
    ]
