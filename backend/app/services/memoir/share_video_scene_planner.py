from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re

from app.config import get_settings
from app.services.ai_factory import get_ai_service
from app.services.json_utils import extract_json_payload


SHARE_VIDEO_SCENE_PLAN_VERSION = "memoir-share-video-scene-plan-v1"
SHARE_VIDEO_SCENE_MIN_DURATION_MS = 6000
SHARE_VIDEO_SCENE_MAX_COUNT = 5
SHARE_VIDEO_SCENE_TEXT_MAX_CHARS = 220


@dataclass(frozen=True)
class ShareVideoScene:
    index: int
    start_char: int
    end_char: int
    start_ms: int
    end_ms: int
    title: str
    visual_summary: str
    image_prompt_content: str


@dataclass(frozen=True)
class ShareVideoScenePlan:
    scenes: list[ShareVideoScene]
    provider: str
    model: str
    strategy: str
    plan_hash: str
    version: str = SHARE_VIDEO_SCENE_PLAN_VERSION


class ShareVideoScenePlanningError(RuntimeError):
    pass


def desired_share_video_scene_count(duration_ms: int) -> int:
    if duration_ms <= 10_000:
        return 1
    if duration_ms <= 25_000:
        return 2
    if duration_ms <= 45_000:
        return 3
    if duration_ms <= 75_000:
        return 4
    return SHARE_VIDEO_SCENE_MAX_COUNT


def _compact_text(text: str, *, limit: int = SHARE_VIDEO_SCENE_TEXT_MAX_CHARS) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _text_hash(text: str, duration_ms: int, max_scene_count: int) -> str:
    return sha256(
        "\n".join((
            SHARE_VIDEO_SCENE_PLAN_VERSION,
            str(duration_ms),
            str(max_scene_count),
            text,
        )).encode("utf-8")
    ).hexdigest()


def _time_for_char(char_timings: list[dict[str, int]], index: int, key: str) -> int:
    if not char_timings:
        return 0
    clamped = min(max(index, 0), len(char_timings) - 1)
    try:
        return int(char_timings[clamped].get(key, 0))
    except (TypeError, ValueError):
        return 0


def _scene_time_range(
    char_timings: list[dict[str, int]],
    start_char: int,
    end_char: int,
    fallback_duration_ms: int,
) -> tuple[int, int]:
    if char_timings and len(char_timings) >= end_char > start_char:
        start_ms = _time_for_char(char_timings, start_char, "start_ms")
        end_ms = _time_for_char(char_timings, end_char - 1, "end_ms")
        if end_ms > start_ms:
            return start_ms, end_ms
    char_count = max(1, len(char_timings) or end_char)
    return (
        round(fallback_duration_ms * start_char / char_count),
        round(fallback_duration_ms * end_char / char_count),
    )


def _preferred_split_points(text: str, count: int) -> list[int]:
    if count <= 1 or not text:
        return [0, len(text)]
    boundaries = [index + 1 for index, char in enumerate(text) if char in "。！？!?；;"]
    points = [0]
    for part in range(1, count):
        target = round(len(text) * part / count)
        candidates = [
            boundary
            for boundary in boundaries
            if points[-1] + 4 <= boundary <= len(text) - 4
        ]
        if candidates:
            split = min(candidates, key=lambda boundary: abs(boundary - target))
        else:
            split = target
        split = min(max(split, points[-1] + 1), len(text) - (count - part))
        points.append(split)
    points.append(len(text))
    return points


def _fallback_title(text: str, index: int) -> str:
    compact = re.sub(r"[，。！？!?；;\s]+", "", text).strip()
    if compact:
        return compact[:12]
    return f"记忆片段 {index + 1}"


def fallback_share_video_scene_plan(
    selected_text: str,
    char_timings: list[dict[str, int]],
    duration_ms: int,
    *,
    max_scene_count: int | None = None,
) -> ShareVideoScenePlan:
    text = selected_text or ""
    desired_count = max_scene_count or desired_share_video_scene_count(duration_ms)
    desired_count = max(1, min(SHARE_VIDEO_SCENE_MAX_COUNT, desired_count, max(1, len(text))))
    if duration_ms < SHARE_VIDEO_SCENE_MIN_DURATION_MS * desired_count:
        desired_count = max(1, duration_ms // SHARE_VIDEO_SCENE_MIN_DURATION_MS)
    points = _preferred_split_points(text, max(1, desired_count))
    scenes: list[ShareVideoScene] = []
    for index, (start_char, end_char) in enumerate(zip(points, points[1:], strict=False)):
        if end_char <= start_char:
            continue
        scene_text = text[start_char:end_char].strip()
        start_ms, end_ms = _scene_time_range(char_timings, start_char, end_char, duration_ms)
        scenes.append(
            ShareVideoScene(
                index=len(scenes),
                start_char=start_char,
                end_char=end_char,
                start_ms=start_ms,
                end_ms=max(start_ms + 1, end_ms),
                title=_fallback_title(scene_text, index),
                visual_summary=f"围绕“{_compact_text(scene_text, limit=80)}”的回忆氛围",
                image_prompt_content=_compact_text(scene_text),
            )
        )
    if not scenes:
        scenes.append(
            ShareVideoScene(
                index=0,
                start_char=0,
                end_char=len(text),
                start_ms=0,
                end_ms=max(1, duration_ms),
                title="回忆片段",
                visual_summary="温暖、克制、留白充足的家庭回忆录氛围",
                image_prompt_content=_compact_text(text)
                or "温暖、克制、留白充足的家庭回忆录背景",
            )
        )
    return ShareVideoScenePlan(
        scenes=scenes,
        provider="fallback",
        model="deterministic",
        strategy="duration_punctuation",
        plan_hash=_text_hash(text, duration_ms, len(scenes)),
    )


def _system_prompt() -> str:
    return (
        "你是回忆录访谈视频的纪录片分镜策划。"
        "你只输出严格 JSON，不要 Markdown，不要解释。"
        "你的任务是把一段口述文字拆成适合生成背景图的少量画面。"
        "不要凭空编造具体正脸、姓名、地标或没有依据的细节；"
        "要提炼年代感、家庭关系、生活场景和情绪。"
    )


def _user_prompt(selected_text: str, duration_ms: int, scene_count: int) -> str:
    return f"""请把以下口述内容规划为 {scene_count} 个视频背景画面。

要求：
1. 输出 JSON 对象，格式为 {{"scenes":[...]}}。
2. scenes 长度必须是 {scene_count}。
3. 每个 scene 包含 start_char、end_char、title、visual_summary、image_prompt_content。
4. start_char/end_char 是在原文里的字符下标，必须覆盖原文且按顺序不重叠。
5. image_prompt_content 要适合交给文生图模型生成背景图，不要包含“生成一张图”这类指令。
6. 不要要求出现文字、字幕、logo、招牌、题字、清晰正脸。
7. 每个画面应保留留白，适合叠加口述文字。

音频时长：{round(duration_ms / 1000, 1)} 秒
原文字符数：{len(selected_text)}

口述内容：
{selected_text}
"""


def _scene_from_payload(
    item: object,
    index: int,
    text: str,
    char_timings: list[dict[str, int]],
    duration_ms: int,
) -> ShareVideoScene | None:
    if not isinstance(item, dict):
        return None
    try:
        start_char = int(item.get("start_char"))
        end_char = int(item.get("end_char"))
    except (TypeError, ValueError):
        return None
    start_char = max(0, min(start_char, len(text)))
    end_char = max(start_char + 1, min(end_char, len(text)))
    if start_char >= len(text):
        return None
    scene_text = text[start_char:end_char]
    title = str(item.get("title") or _fallback_title(scene_text, index)).strip()[:30]
    visual_summary = str(item.get("visual_summary") or "").strip()
    prompt_content = str(item.get("image_prompt_content") or "").strip()
    start_ms, end_ms = _scene_time_range(char_timings, start_char, end_char, duration_ms)
    return ShareVideoScene(
        index=index,
        start_char=start_char,
        end_char=end_char,
        start_ms=start_ms,
        end_ms=max(start_ms + 1, end_ms),
        title=title or _fallback_title(scene_text, index),
        visual_summary=_compact_text(visual_summary or scene_text),
        image_prompt_content=_compact_text(prompt_content or scene_text),
    )


def _normalize_llm_scenes(
    payload: object,
    text: str,
    char_timings: list[dict[str, int]],
    duration_ms: int,
    expected_count: int,
) -> list[ShareVideoScene]:
    if not isinstance(payload, dict):
        return []
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list):
        return []
    scenes = [
        scene
        for index, item in enumerate(raw_scenes)
        if (scene := _scene_from_payload(item, index, text, char_timings, duration_ms))
    ]
    if len(scenes) != expected_count:
        return []
    normalized: list[ShareVideoScene] = []
    previous_end = 0
    for index, scene in enumerate(scenes):
        start_char = previous_end if index else 0
        end_char = scene.end_char if index < len(scenes) - 1 else len(text)
        if end_char <= start_char:
            return []
        start_ms, end_ms = _scene_time_range(char_timings, start_char, end_char, duration_ms)
        normalized.append(
            ShareVideoScene(
                index=index,
                start_char=start_char,
                end_char=end_char,
                start_ms=start_ms,
                end_ms=max(start_ms + 1, end_ms),
                title=scene.title,
                visual_summary=scene.visual_summary,
                image_prompt_content=scene.image_prompt_content,
            )
        )
        previous_end = end_char
    return normalized


async def plan_share_video_background_scenes(
    selected_text: str,
    char_timings: list[dict[str, int]],
    duration_ms: int,
    *,
    max_scene_count: int | None = None,
) -> ShareVideoScenePlan:
    settings = get_settings()
    desired_count = desired_share_video_scene_count(duration_ms)
    if max_scene_count is not None:
        desired_count = min(desired_count, max_scene_count)
    desired_count = max(1, min(SHARE_VIDEO_SCENE_MAX_COUNT, desired_count))
    if desired_count <= 1 or not settings.memoir_share_video_scene_planner_enabled:
        return fallback_share_video_scene_plan(
            selected_text,
            char_timings,
            duration_ms,
            max_scene_count=desired_count,
        )

    model = settings.memoir_share_video_scene_planner_model.strip() or None
    try:
        client = get_ai_service(model)
        content = await client._call_llm(
            _system_prompt(),
            _user_prompt(selected_text, duration_ms, desired_count),
            temperature=0.2,
            max_tokens=1600,
            timeout=settings.memoir_share_video_scene_planner_timeout_seconds,
        )
        payload = extract_json_payload(str(content))
        scenes = _normalize_llm_scenes(
            payload,
            selected_text,
            char_timings,
            duration_ms,
            desired_count,
        )
        if not scenes:
            raise ShareVideoScenePlanningError("LLM scene plan is empty or invalid")
        return ShareVideoScenePlan(
            scenes=scenes,
            provider=client.provider_name,
            model=client.model_name,
            strategy="llm",
            plan_hash=_text_hash(selected_text, duration_ms, len(scenes)),
        )
    except Exception:
        return fallback_share_video_scene_plan(
            selected_text,
            char_timings,
            duration_ms,
            max_scene_count=desired_count,
        )


def scene_plan_to_metadata(plan: ShareVideoScenePlan) -> dict[str, object]:
    return {
        "version": plan.version,
        "provider": plan.provider,
        "model": plan.model,
        "strategy": plan.strategy,
        "plan_hash": plan.plan_hash,
        "scenes": [
            {
                "index": scene.index,
                "start_char": scene.start_char,
                "end_char": scene.end_char,
                "start_ms": scene.start_ms,
                "end_ms": scene.end_ms,
                "title": scene.title,
                "visual_summary": scene.visual_summary,
                "image_prompt_content": scene.image_prompt_content,
            }
            for scene in plan.scenes
        ],
    }
