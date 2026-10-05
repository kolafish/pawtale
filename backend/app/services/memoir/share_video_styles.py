from __future__ import annotations

from typing import Literal


ShareVideoStyle = Literal["uploaded", "illustrated", "story_animation"]
DEFAULT_SHARE_VIDEO_STYLE: ShareVideoStyle = "illustrated"
SHARE_VIDEO_STYLE_STORY_ANIMATION: ShareVideoStyle = "story_animation"


def normalize_share_video_style(value: object) -> ShareVideoStyle:
    # Older projects and jobs have no style field and use the existing renderer.
    if value == "uploaded":
        return "uploaded"
    return "story_animation" if value == "story_animation" else DEFAULT_SHARE_VIDEO_STYLE


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
        {
            "value": "story_animation",
            "label": "往事小剧场",
            "description": "用人物动作、场景变化和连贯镜头，陪着原声讲故事。",
        },
    ]
