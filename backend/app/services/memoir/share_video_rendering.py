from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


_VIDEO_WIDTH = 1280
_VIDEO_HEIGHT = 720
_BACKGROUND_COLOR = "0xf6f1e8"
_SHARE_VIDEO_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_SHARE_VIDEO_FONT_FILE = "NotoSerifSC-Regular.ttf"
_SHARE_VIDEO_FONT_NAME = "Memoir Noto Serif SC"
_ASS_INK_COLOR = "&H00403A2F"
_ASS_TEA_GOLD_COLOR = "&H003A90B9"
_ASS_MUTED_COLOR = "&H00919FA8"
_ASS_RULE_COLOR = "&H00BBCDD8"
_QUOTE_PAGE_MAX_CHARS = 36
_QUOTE_LINE_MAX_CHARS = 12
_QUOTE_PHRASE_MAX_CHARS = 3
_QUOTE_MIN_FRAGMENT_CHARS = 4
_QUOTE_STRONG_BREAK_CHARS = "。！？!?；;\n"
_QUOTE_SOFT_BREAK_CHARS = "，,、"
_QUOTE_PUNCTUATION_CHARS = "，,、。！？!?；;：“”‘’\"'（）()[]【】《》…—- \t\r\n"


@dataclass(frozen=True)
class ShareVideoBackgroundSegment:
    image_path: Path
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class ShareVideoRenderRequest:
    source_audio_path: Path
    source_audio_mime_type: str
    target_path: Path
    selected_text: str
    char_timings: list[dict[str, int]]
    start_ms: int
    end_ms: int
    background_image_path: Path | None = None
    background_segments: list[ShareVideoBackgroundSegment] | None = None


class ShareVideoGenerationError(RuntimeError):
    pass


def _ffmpeg_executable() -> str:
    configured = os.environ.get("FFMPEG_BINARY", "").strip()
    if configured:
        return configured
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
    except Exception as exc:
        raise ShareVideoGenerationError("视频编码器不可用") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


@lru_cache(maxsize=16)
def _ffmpeg_filter_available(ffmpeg: str, filter_name: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    marker = f" {filter_name} "
    return any(marker in line for line in result.stdout.splitlines())


async def _run_ffmpeg(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        raise
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:]
        raise ShareVideoGenerationError(message[0] if message else "ffmpeg 执行失败")


def _ass_time(ms: int) -> str:
    centiseconds = max(0, round(ms / 10))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "")


def _subtitle_ranges(text: str, *, max_chars: int = 24) -> list[tuple[int, int]]:
    phrase_ranges = _quote_phrase_page_ranges(text)
    pages: list[tuple[int, int]] = []
    page_start: int | None = None
    page_end: int | None = None
    page_chars = 0
    for phrase_start, phrase_end in phrase_ranges:
        phrase_chars = _visible_range_length(text, phrase_start, phrase_end)
        if phrase_chars > max_chars:
            if page_start is not None and page_end is not None:
                pages.append((page_start, page_end))
                page_start = None
                page_end = None
                page_chars = 0
            pages.extend(_split_quote_range(text, phrase_start, phrase_end, max_chars=max_chars))
            continue
        if (
            page_start is not None
            and page_end is not None
            and page_chars + phrase_chars > max_chars
            and page_chars >= _QUOTE_MIN_FRAGMENT_CHARS
        ):
            pages.append((page_start, page_end))
            page_start = None
            page_end = None
            page_chars = 0
        if page_start is None:
            page_start = phrase_start
        page_end = phrase_end
        page_chars += phrase_chars
    if page_start is not None and page_end is not None:
        pages.append((page_start, page_end))
    return _merge_tiny_quote_ranges(text, pages)


def _visible_range_length(text: str, start: int, end: int) -> int:
    return sum(1 for char in text[start:end] if not char.isspace())


def _is_tiny_quote_range(text: str, start: int, end: int) -> bool:
    fragment = text[start:end].strip()
    if not fragment:
        return True
    return (
        _visible_range_length(fragment, 0, len(fragment)) < _QUOTE_MIN_FRAGMENT_CHARS
        or all(char in _QUOTE_PUNCTUATION_CHARS for char in fragment)
    )


def _merge_tiny_quote_ranges(text: str, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        start, end = _strip_range(text, start, end)
        if start >= end:
            continue
        if merged and _is_tiny_quote_range(text, start, end):
            previous_start, _previous_end = merged[-1]
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    if len(merged) > 1 and _is_tiny_quote_range(text, merged[0][0], merged[0][1]):
        first_start, _first_end = merged.pop(0)
        next_start, next_end = merged[0]
        merged[0] = (first_start, next_end)
    return merged


def _quote_phrase_page_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char in _QUOTE_STRONG_BREAK_CHARS or char in _QUOTE_SOFT_BREAK_CHARS:
            ranges.append((start, index + 1))
            start = index + 1
    if start < len(text):
        ranges.append((start, len(text)))
    return _merge_tiny_quote_ranges(text, ranges)


def _split_quote_range(text: str, start: int, end: int, *, max_chars: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        if _visible_range_length(text, cursor, end) <= max_chars:
            ranges.append((cursor, end))
            break
        target = min(end, cursor + max_chars)
        candidates = [
            index + 1
            for index in range(cursor, min(end, target + 1))
            if text[index] in _QUOTE_SOFT_BREAK_CHARS
            and _visible_range_length(text, cursor, index + 1) >= _QUOTE_MIN_FRAGMENT_CHARS
            and _visible_range_length(text, index + 1, end) >= _QUOTE_MIN_FRAGMENT_CHARS
        ]
        split = max(candidates) if candidates else target
        while split < end and text[split] in _QUOTE_PUNCTUATION_CHARS:
            split += 1
        if split <= cursor:
            ranges.append((cursor, end))
            break
        ranges.append((cursor, split))
        cursor = split
    return _merge_tiny_quote_ranges(text, ranges)


def _strip_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _preferred_compact_line_break(text: str, start: int, end: int) -> int | None:
    length = end - start
    if length <= 8:
        return None
    midpoint = start + length / 2
    candidates = [
        index
        for index in range(start + 3, end - 3)
        if text[index] in "与和但而却"
    ]
    if candidates:
        return min(candidates, key=lambda index: abs(index - midpoint))
    return start + round(length / 2)


def _quote_line_ranges(text: str, start: int, end: int) -> list[tuple[int, int]]:
    start, end = _strip_range(text, start, end)
    if end <= start:
        return []
    if end - start <= 16 and "\n" not in text[start:end]:
        compact_break = _preferred_compact_line_break(text, start, end)
        if compact_break:
            return [
                item
                for item in (
                    _strip_range(text, start, compact_break),
                    _strip_range(text, compact_break, end),
                )
                if item[0] < item[1]
            ]

    lines: list[tuple[int, int]] = []
    line_start = start
    last_soft_break: int | None = None
    for index in range(start, end):
        char = text[index]
        if char in "，,、；;":
            last_soft_break = index + 1
        if char == "\n":
            line_end = index
        elif char in "。！？!?" or index - line_start + 1 >= _QUOTE_LINE_MAX_CHARS:
            line_end = last_soft_break if last_soft_break and last_soft_break > line_start else index + 1
        else:
            continue
        line_range = _strip_range(text, line_start, line_end)
        if line_range[0] < line_range[1]:
            lines.append(line_range)
        line_start = index + 1 if char == "\n" else line_end
        last_soft_break = None
    if line_start < end:
        line_range = _strip_range(text, line_start, end)
        if line_range[0] < line_range[1]:
            lines.append(line_range)
    return _merge_awkward_quote_lines(text, lines)


def _merge_awkward_quote_lines(text: str, lines: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in lines:
        start, end = _strip_range(text, start, end)
        if start >= end:
            continue
        if merged:
            previous_start, previous_end = merged[-1]
            while start < end and text[start] in _QUOTE_PUNCTUATION_CHARS:
                previous_end = start + 1
                start += 1
            merged[-1] = (previous_start, previous_end)
            start, end = _strip_range(text, start, end)
            if start >= end:
                continue
        if merged and _is_tiny_quote_range(text, start, end):
            previous_start, _previous_end = merged[-1]
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    if len(merged) > 1 and _is_tiny_quote_range(text, merged[0][0], merged[0][1]):
        first_start, _first_end = merged.pop(0)
        next_start, next_end = merged[0]
        merged[0] = (first_start, next_end)
    return merged


def _karaoke_phrase_ranges(text: str, start: int, end: int) -> list[tuple[int, int]]:
    phrases: list[tuple[int, int]] = []
    phrase_start = start
    visible_chars = 0
    for index in range(start, end):
        char = text[index]
        visible_chars += 0 if char.isspace() else 1
        should_break = char in "，,、。！？!?；;" or visible_chars >= _QUOTE_PHRASE_MAX_CHARS
        if should_break:
            phrase_range = _strip_range(text, phrase_start, index + 1)
            if phrase_range[0] < phrase_range[1]:
                phrases.append(phrase_range)
            phrase_start = index + 1
            visible_chars = 0
    if phrase_start < end:
        phrase_range = _strip_range(text, phrase_start, end)
        if phrase_range[0] < phrase_range[1]:
            phrases.append(phrase_range)
    return phrases


def _linear_timings(text: str, duration_ms: int) -> list[dict[str, int]]:
    char_count = max(1, len(text))
    return [
        {
            "start_ms": round(duration_ms * index / char_count),
            "end_ms": round(duration_ms * (index + 1) / char_count),
        }
        for index, _char in enumerate(text)
    ]


def _ass_text(text: str) -> str:
    return _ass_escape(text).replace("\n", " ")


def _decorative_subtitle_events(duration_ms: int) -> list[str]:
    end_time = _ass_time(duration_ms)
    return [
        f"Dialogue: 0,0:00:00.00,{end_time},Meta,,0,0,0,,{{\\an8\\pos(640,102)\\fad(160,160)}}回忆录访谈 · 原声摘录\n",
        f"Dialogue: 0,0:00:00.00,{end_time},Rule,,0,0,0,,{{\\an7\\pos(500,144)\\alpha&H55&\\p1}}m 0 0 l 280 0 l 280 2 l 0 2\n",
        f"Dialogue: 0,0:00:00.00,{end_time},Mark,,0,0,0,,{{\\an7\\pos(238,226)\\alpha&H80&}}“\n",
        f"Dialogue: 0,0:00:00.00,{end_time},Meta,,0,0,0,,{{\\an2\\pos(640,618)\\fad(160,160)}}口述片段\n",
        f"Dialogue: 0,0:00:00.00,{end_time},Rule,,0,0,0,,{{\\an7\\pos(500,646)\\alpha&H66&\\p1}}m 0 0 l 280 0 l 280 2 l 0 2\n",
    ]


def _quote_karaoke_text(
    text: str,
    lines: list[tuple[int, int]],
    char_timings: list[dict[str, int]],
) -> str:
    pieces: list[str] = [r"{\an5\pos(640,356)\q2\fad(180,180)}"]
    for line_index, (line_start, line_end) in enumerate(lines):
        if line_index:
            pieces.append(r"\N")
        for phrase_start, phrase_end in _karaoke_phrase_ranges(text, line_start, line_end):
            phrase_duration = (
                int(char_timings[phrase_end - 1]["end_ms"])
                - int(char_timings[phrase_start]["start_ms"])
            )
            pieces.append(
                r"{\kf"
                + str(max(1, round(phrase_duration / 10)))
                + "}"
                + _ass_text(text[phrase_start:phrase_end])
            )
    return "".join(pieces)


def _subtitle_file_content(request: ShareVideoRenderRequest) -> str:
    duration_ms = max(1, request.end_ms - request.start_ms)
    char_timings = request.char_timings
    if len(char_timings) != len(request.selected_text):
        char_timings = _linear_timings(request.selected_text, duration_ms)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Quote,{_SHARE_VIDEO_FONT_NAME},62,{_ASS_TEA_GOLD_COLOR},{_ASS_INK_COLOR},{_ASS_INK_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,120,120,80,1
Style: Meta,{_SHARE_VIDEO_FONT_NAME},28,{_ASS_MUTED_COLOR},{_ASS_MUTED_COLOR},{_ASS_MUTED_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,80,80,80,1
Style: Mark,{_SHARE_VIDEO_FONT_NAME},112,{_ASS_RULE_COLOR},{_ASS_RULE_COLOR},{_ASS_RULE_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Rule,{_SHARE_VIDEO_FONT_NAME},10,{_ASS_RULE_COLOR},{_ASS_RULE_COLOR},{_ASS_RULE_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [header, *_decorative_subtitle_events(duration_ms)]
    for start, end in _subtitle_ranges(request.selected_text, max_chars=_QUOTE_PAGE_MAX_CHARS):
        lines = _quote_line_ranges(request.selected_text, start, end)
        if not lines:
            continue
        start_ms = max(0, int(char_timings[start]["start_ms"]) - request.start_ms)
        end_ms = max(start_ms + 1, int(char_timings[end - 1]["end_ms"]) - request.start_ms)
        events.append(
            f"Dialogue: 1,{_ass_time(start_ms)},{_ass_time(end_ms)},Quote,,0,0,0,,"
            f"{_quote_karaoke_text(request.selected_text, lines, char_timings)}\n"
        )
    return "".join(events)


def _escaped_subtitle_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _share_video_font_dir() -> Path:
    font_path = _SHARE_VIDEO_FONT_DIR / _SHARE_VIDEO_FONT_FILE
    if not font_path.exists():
        raise ShareVideoGenerationError("分享视频中文字体文件缺失")
    return _SHARE_VIDEO_FONT_DIR


def _subtitle_filter_argument(subtitle_path: Path) -> str:
    font_dir = _share_video_font_dir()
    subtitle = _escaped_subtitle_filter_path(subtitle_path)
    fonts = _escaped_subtitle_filter_path(font_dir)
    return f"subtitles=filename='{subtitle}':fontsdir='{fonts}'"


def _video_filter_argument(subtitle_path: Path, *, has_background_image: bool = False) -> str:
    filters: list[str] = []
    if has_background_image:
        filters.append(
            f"scale={_VIDEO_WIDTH}:{_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={_VIDEO_WIDTH}:{_VIDEO_HEIGHT}"
        )
    filters.append(_subtitle_filter_argument(subtitle_path))
    return ",".join(filters)


def _valid_background_segments(
    request: ShareVideoRenderRequest,
) -> list[ShareVideoBackgroundSegment]:
    segments = request.background_segments or []
    return [
        segment
        for segment in segments
        if segment.image_path.exists()
        and segment.image_path.stat().st_size > 0
        and segment.end_ms > segment.start_ms
    ]


def _multi_background_filter_complex(
    segments: list[ShareVideoBackgroundSegment],
    subtitle_path: Path,
    *,
    use_xfade: bool = True,
) -> str:
    transition_seconds = (
        min(
            0.6,
            max(0.0, min((segment.end_ms - segment.start_ms) / 1000 for segment in segments) / 2),
        )
        if use_xfade
        else 0.0
    )
    parts: list[str] = []
    raw_durations = [
        max(0.1, (segment.end_ms - segment.start_ms) / 1000)
        for segment in segments
    ]
    for index, raw_duration in enumerate(raw_durations):
        duration = raw_duration + (transition_seconds if index < len(raw_durations) - 1 else 0)
        parts.append(
            f"[{index}:v]"
            f"scale={_VIDEO_WIDTH}:{_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={_VIDEO_WIDTH}:{_VIDEO_HEIGHT},setsar=1,"
            f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"fps=30,format=yuv420p,settb=AVTB[v{index}]"
        )

    current_label = "v0"
    if use_xfade and len(segments) > 1 and transition_seconds > 0:
        elapsed = raw_durations[0]
        for index in range(1, len(segments)):
            next_label = f"x{index}"
            parts.append(
                f"[{current_label}][v{index}]"
                f"xfade=transition=fade:duration={transition_seconds:.3f}:offset={elapsed:.3f}"
                f"[{next_label}]"
            )
            current_label = next_label
            elapsed += raw_durations[index]
    elif len(segments) > 1:
        labels = "".join(f"[v{index}]" for index in range(len(segments)))
        parts.append(f"{labels}concat=n={len(segments)}:v=1:a=0[concatbg]")
        current_label = "concatbg"

    parts.append(f"[{current_label}]{_subtitle_filter_argument(subtitle_path)}[vout]")
    return ";".join(parts)


async def render_share_video_clip(request: ShareVideoRenderRequest) -> None:
    ffmpeg = _ffmpeg_executable()
    request.target_path.parent.mkdir(parents=True, exist_ok=True)
    duration_seconds = max(0.1, (request.end_ms - request.start_ms) / 1000)
    with tempfile.TemporaryDirectory(prefix="memoir-share-video-") as tmp:
        tmpdir = Path(tmp)
        subtitle_path = tmpdir / "subtitles.ass"
        clip_audio_path = tmpdir / "clip.wav"
        temp_video_path = tmpdir / "video.mp4"
        subtitle_path.write_text(_subtitle_file_content(request), encoding="utf-8")

        audio_input_args = []
        if request.source_audio_mime_type == "audio/pcm":
            audio_input_args.extend(["-f", "s16le", "-ar", "16000", "-ac", "1"])
        audio_input_args.extend(["-i", str(request.source_audio_path)])
        await _run_ffmpeg([
            ffmpeg,
            "-y",
            *audio_input_args,
            "-ss",
            f"{request.start_ms / 1000:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-c:a",
            "pcm_s16le",
            str(clip_audio_path),
        ])

        background_segments = _valid_background_segments(request)
        if len(background_segments) > 1:
            video_input_args: list[str] = []
            for segment in background_segments:
                duration_seconds_for_input = max(0.1, (segment.end_ms - segment.start_ms) / 1000 + 0.7)
                video_input_args.extend([
                    "-loop",
                    "1",
                    "-t",
                    f"{duration_seconds_for_input:.3f}",
                    "-i",
                    str(segment.image_path),
                ])
            audio_input_index = len(background_segments)
            await _run_ffmpeg([
                ffmpeg,
                "-y",
                *video_input_args,
                "-i",
                str(clip_audio_path),
                "-filter_complex",
                _multi_background_filter_complex(
                    background_segments,
                    subtitle_path,
                    use_xfade=_ffmpeg_filter_available(ffmpeg, "xfade"),
                ),
                "-map",
                "[vout]",
                "-map",
                f"{audio_input_index}:a",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                str(temp_video_path),
            ])
            temp_video_path.replace(request.target_path)
            return

        single_background_image_path = request.background_image_path
        if not single_background_image_path and len(background_segments) == 1:
            single_background_image_path = background_segments[0].image_path
        has_background_image = bool(
            single_background_image_path
            and single_background_image_path.exists()
            and single_background_image_path.stat().st_size > 0
        )
        if has_background_image:
            video_input_args = [
                "-loop",
                "1",
                "-t",
                f"{duration_seconds:.3f}",
                "-i",
                str(single_background_image_path),
            ]
        else:
            video_input_args = [
                "-f",
                "lavfi",
                "-i",
                f"color=c={_BACKGROUND_COLOR}:s={_VIDEO_WIDTH}x{_VIDEO_HEIGHT}:d={duration_seconds:.3f}",
            ]

        await _run_ffmpeg([
            ffmpeg,
            "-y",
            *video_input_args,
            "-i",
            str(clip_audio_path),
            "-vf",
            _video_filter_argument(subtitle_path, has_background_image=has_background_image),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(temp_video_path),
        ])
        temp_video_path.replace(request.target_path)
