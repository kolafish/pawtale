from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re

from app.config import get_settings
from app.services.ai_factory import get_ai_service
from app.services.json_utils import extract_json_payload


SHARE_VIDEO_SCENE_PLAN_VERSION = "memoir-share-video-scene-plan-v2"
SHARE_VIDEO_SCENE_MIN_DURATION_MS = 6000
SHARE_VIDEO_SCENE_MAX_COUNT = 5
SHARE_VIDEO_SCENE_TEXT_MAX_CHARS = 220
SHARE_VIDEO_SENTENCE_MAX_CHARS = 58
SHARE_VIDEO_SENTENCE_MIN_CHARS = 6
_STRONG_BREAK_CHARS = "。！？!?；;\n"
_SOFT_BREAK_CHARS = "，,、"
_PUNCTUATION_CHARS = "，,、。！？!?；;：“”‘’\"'（）()[]【】《》…—- \t\r\n"


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
    sentence_ids: tuple[int, ...] = ()
    evidence_quote: str = ""


@dataclass(frozen=True)
class ShareVideoSentence:
    index: int
    start_char: int
    end_char: int
    start_ms: int
    end_ms: int
    text: str


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


def _visible_text_length(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _is_tiny_or_punctuation_only(text: str, *, min_chars: int = SHARE_VIDEO_SENTENCE_MIN_CHARS) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return _visible_text_length(stripped) < min_chars or all(char in _PUNCTUATION_CHARS for char in stripped)


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


def _initial_sentence_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char in _STRONG_BREAK_CHARS:
            ranges.append((start, index + 1))
            start = index + 1
    if start < len(text):
        ranges.append((start, len(text)))
    return ranges


def _range_visible_length(text: str, start: int, end: int) -> int:
    return _visible_text_length(text[start:end])


def _split_long_sentence_range(
    text: str,
    start: int,
    end: int,
    *,
    max_chars: int = SHARE_VIDEO_SENTENCE_MAX_CHARS,
    min_chars: int = SHARE_VIDEO_SENTENCE_MIN_CHARS,
) -> list[tuple[int, int]]:
    if _range_visible_length(text, start, end) <= max_chars:
        return [(start, end)]

    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        remaining = _range_visible_length(text, cursor, end)
        if remaining <= max_chars or remaining <= max_chars + min_chars:
            ranges.append((cursor, end))
            break

        target = cursor + max_chars
        candidates = [
            index + 1
            for index in range(cursor, end)
            if text[index] in _SOFT_BREAK_CHARS
            and _range_visible_length(text, cursor, index + 1) >= min_chars
            and _range_visible_length(text, index + 1, end) >= min_chars
        ]
        before_target = [candidate for candidate in candidates if candidate <= target]
        if before_target:
            split = max(before_target)
        elif candidates:
            split = min(candidates, key=lambda candidate: abs(candidate - target))
        else:
            split = min(end, cursor + max_chars)
        if split <= cursor:
            ranges.append((cursor, end))
            break
        ranges.append((cursor, split))
        cursor = split
    return ranges


def _merge_tiny_ranges(text: str, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if not text[start:end].strip():
            continue
        if merged and _is_tiny_or_punctuation_only(text[start:end]):
            previous_start, _previous_end = merged[-1]
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))

    if len(merged) > 1 and _is_tiny_or_punctuation_only(text[merged[0][0]:merged[0][1]]):
        first_start, _first_end = merged.pop(0)
        next_start, next_end = merged[0]
        merged[0] = (first_start, next_end)
    return merged


def share_video_sentence_units(
    selected_text: str,
    char_timings: list[dict[str, int]],
    duration_ms: int,
) -> list[ShareVideoSentence]:
    text = selected_text or ""
    raw_ranges: list[tuple[int, int]] = []
    for start, end in _initial_sentence_ranges(text):
        raw_ranges.extend(_split_long_sentence_range(text, start, end))
    ranges = _merge_tiny_ranges(text, raw_ranges)

    sentences: list[ShareVideoSentence] = []
    for start_char, end_char in ranges:
        start_ms, end_ms = _scene_time_range(char_timings, start_char, end_char, duration_ms)
        sentences.append(
            ShareVideoSentence(
                index=len(sentences),
                start_char=start_char,
                end_char=end_char,
                start_ms=start_ms,
                end_ms=max(start_ms + 1, end_ms),
                text=text[start_char:end_char].strip(),
            )
        )
    return sentences


def _scene_count_range(
    duration_ms: int,
    sentence_count: int,
    max_scene_count: int | None,
) -> tuple[int, int]:
    if sentence_count <= 0:
        return 1, 1
    upper = min(SHARE_VIDEO_SCENE_MAX_COUNT, sentence_count, desired_share_video_scene_count(duration_ms))
    if max_scene_count is not None:
        upper = min(upper, max_scene_count)
    if duration_ms < SHARE_VIDEO_SCENE_MIN_DURATION_MS * upper:
        upper = max(1, duration_ms // SHARE_VIDEO_SCENE_MIN_DURATION_MS)
    upper = max(1, upper)
    lower = 1 if upper == 1 else 2
    return lower, upper


def _group_sentence_units(
    sentences: list[ShareVideoSentence],
    count: int,
) -> list[list[ShareVideoSentence]]:
    if not sentences:
        return []
    count = max(1, min(count, len(sentences)))
    weights = [max(1, _visible_text_length(sentence.text)) for sentence in sentences]
    prefix = [0]
    for weight in weights:
        prefix.append(prefix[-1] + weight)

    groups: list[list[ShareVideoSentence]] = []
    cursor = 0
    total = prefix[-1]
    for group_index in range(count):
        remaining_groups = count - group_index
        if remaining_groups == 1:
            next_cursor = len(sentences)
        else:
            min_next = cursor + 1
            max_next = len(sentences) - remaining_groups + 1
            target = total * (group_index + 1) / count
            next_cursor = min(
                range(min_next, max_next + 1),
                key=lambda candidate: abs(prefix[candidate] - target),
            )
        groups.append(sentences[cursor:next_cursor])
        cursor = next_cursor
    return groups


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
    sentences = share_video_sentence_units(text, char_timings, duration_ms)
    _min_count, upper_count = _scene_count_range(duration_ms, len(sentences), max_scene_count)
    desired_count = min(upper_count, max_scene_count or desired_share_video_scene_count(duration_ms))
    scenes: list[ShareVideoScene] = []
    for index, group in enumerate(_group_sentence_units(sentences, max(1, desired_count))):
        if not group:
            continue
        start_char = group[0].start_char
        end_char = group[-1].end_char
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
                sentence_ids=tuple(sentence.index for sentence in group),
                evidence_quote=_compact_text(scene_text, limit=90),
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
                sentence_ids=(),
                evidence_quote=_compact_text(text, limit=90),
            )
        )
    return ShareVideoScenePlan(
        scenes=scenes,
        provider="fallback",
        model="deterministic",
        strategy="sentence_units",
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


def _user_prompt(
    selected_text: str,
    sentences: list[ShareVideoSentence],
    duration_ms: int,
    min_scene_count: int,
    max_scene_count: int,
) -> str:
    sentence_lines = "\n".join(
        f"{sentence.index}. [{round(sentence.start_ms / 1000, 1)}s-"
        f"{round(sentence.end_ms / 1000, 1)}s] {sentence.text}"
        for sentence in sentences
    )
    return f"""请把以下完整口述内容规划为 {min_scene_count} 到 {max_scene_count} 个视频背景画面，由你根据内容自然决定具体数量。

要求：
1. 输出 JSON 对象，格式为 {{"scenes":[...]}}。
2. scenes 长度必须在 {min_scene_count} 到 {max_scene_count} 之间；不要为了平均切分而机械用满上限。
3. 每个 scene 包含 sentence_ids、title、evidence_quote、visual_summary、image_prompt_content。
4. sentence_ids 只能引用下面的“句子/短句单元”编号，必须连续、递增、不重叠，并且所有编号从 0 到 {len(sentences) - 1} 必须刚好覆盖一次。
5. 不要拆开任何一个句子/短句单元；不要输出 start_char/end_char。
6. evidence_quote 应来自本 scene 覆盖的句子/短句，可以适度压缩但不要改写事实。
7. image_prompt_content 要适合交给文生图模型生成背景图，不要包含“生成一张图”这类指令。
8. image_prompt_content 只能提炼原文支持的可视化场景、年代感、家庭关系、生活物件和情绪；不要凭空编造具体年份、地点、人物身份或清晰正脸。
9. 不要要求出现文字、字幕、logo、招牌、题字、清晰正脸。
10. 每个画面应保留留白，适合叠加口述文字。

音频时长：{round(duration_ms / 1000, 1)} 秒
原文字符数：{len(selected_text)}

口述内容：
{selected_text}

句子/短句单元：
{sentence_lines}
"""


def _scene_from_sentence_payload(
    item: object,
    index: int,
    text: str,
    sentences: list[ShareVideoSentence],
    sentence_ids: list[int],
    char_timings: list[dict[str, int]],
    duration_ms: int,
) -> ShareVideoScene | None:
    if not isinstance(item, dict):
        return None
    if not sentence_ids:
        return None
    start_char = sentences[sentence_ids[0]].start_char
    end_char = sentences[sentence_ids[-1]].end_char
    if start_char >= len(text):
        return None
    scene_text = text[start_char:end_char]
    title = str(item.get("title") or _fallback_title(scene_text, index)).strip()[:30]
    visual_summary = str(item.get("visual_summary") or "").strip()
    evidence_quote = str(item.get("evidence_quote") or "").strip()
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
        sentence_ids=tuple(sentence_ids),
        evidence_quote=_compact_text(evidence_quote or scene_text, limit=90),
    )


def _parse_sentence_ids(value: object) -> list[int] | None:
    if not isinstance(value, list) or not value:
        return None
    sentence_ids: list[int] = []
    for item in value:
        try:
            sentence_ids.append(int(item))
        except (TypeError, ValueError):
            return None
    return sentence_ids


def _normalize_llm_sentence_scenes(
    payload: object,
    text: str,
    sentences: list[ShareVideoSentence],
    char_timings: list[dict[str, int]],
    duration_ms: int,
    min_scene_count: int,
    max_scene_count: int,
) -> list[ShareVideoScene]:
    if not isinstance(payload, dict):
        return []
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list):
        return []
    if not (min_scene_count <= len(raw_scenes) <= max_scene_count):
        return []
    scenes: list[ShareVideoScene] = []
    next_sentence_id = 0
    for index, item in enumerate(raw_scenes):
        if not isinstance(item, dict):
            return []
        sentence_ids = _parse_sentence_ids(item.get("sentence_ids"))
        if sentence_ids is None:
            return []
        expected_ids = list(range(next_sentence_id, next_sentence_id + len(sentence_ids)))
        if sentence_ids != expected_ids:
            return []
        if sentence_ids[-1] >= len(sentences):
            return []
        scene = _scene_from_sentence_payload(
            item,
            index,
            text,
            sentences,
            sentence_ids,
            char_timings,
            duration_ms,
        )
        if scene is None:
            return []
        scenes.append(scene)
        next_sentence_id += len(sentence_ids)
    if next_sentence_id != len(sentences):
        return []
    return scenes


async def plan_share_video_background_scenes(
    selected_text: str,
    char_timings: list[dict[str, int]],
    duration_ms: int,
    *,
    max_scene_count: int | None = None,
) -> ShareVideoScenePlan:
    settings = get_settings()
    sentences = share_video_sentence_units(selected_text, char_timings, duration_ms)
    min_count, max_count = _scene_count_range(duration_ms, len(sentences), max_scene_count)
    if max_count <= 1 or not settings.memoir_share_video_scene_planner_enabled:
        return fallback_share_video_scene_plan(
            selected_text,
            char_timings,
            duration_ms,
            max_scene_count=max_count,
        )

    model = settings.memoir_share_video_scene_planner_model.strip() or None
    try:
        client = get_ai_service(model)
        content = await client._call_llm(
            _system_prompt(),
            _user_prompt(selected_text, sentences, duration_ms, min_count, max_count),
            temperature=0.2,
            max_tokens=2200,
            timeout=settings.memoir_share_video_scene_planner_timeout_seconds,
        )
        payload = extract_json_payload(str(content))
        scenes = _normalize_llm_sentence_scenes(
            payload,
            selected_text,
            sentences,
            char_timings,
            duration_ms,
            min_count,
            max_count,
        )
        if not scenes:
            raise ShareVideoScenePlanningError("LLM scene plan is empty or invalid")
        return ShareVideoScenePlan(
            scenes=scenes,
            provider=client.provider_name,
            model=client.model_name,
            strategy="llm_sentence_units",
            plan_hash=_text_hash(selected_text, duration_ms, len(scenes)),
        )
    except Exception:
        return fallback_share_video_scene_plan(
            selected_text,
            char_timings,
            duration_ms,
            max_scene_count=max_count,
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
                "sentence_ids": list(scene.sentence_ids),
                "evidence_quote": scene.evidence_quote,
            }
            for scene in plan.scenes
        ],
    }
