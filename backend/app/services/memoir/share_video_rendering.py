"""Vertical share video: original narration, moving pictures, spoken captions.

Every frame is composed here and piped to ffmpeg, so pictures, the opening hook
sentence, captions and the closing card share one layout. The middle of the
frame holds the picture's subject; captions sit in the calm lower band above
WeChat's own bottom overlay.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
import io
import math
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.services.memoir.branding import render_brand_lockup


_VIDEO_WIDTH = 720
_VIDEO_HEIGHT = 1280
_FPS = 30
# Pictures are prepared slightly larger than the frame so the camera can move.
_CANVAS_SCALE = 1.18
_SUBJECT_CENTER_Y = 0.42
_SUBTITLE_CENTER_Y = 0.74
_SHOT_TARGET_SECONDS = 6.0
_CROSSFADE_SECONDS = 0.8
_END_CARD_SECONDS = 3.2
_SHARE_VIDEO_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_SHARE_VIDEO_FONT_FILE = "NotoSerifSC-Regular.ttf"
_SHARE_VIDEO_BOLD_FONT_FILE = "NotoSerifSC-Bold.ttf"
_QUOTE_PAGE_MAX_CHARS = 24
_QUOTE_LINE_MAX_CHARS = 12
_QUOTE_PAGE_MAX_SENTENCES = 2
_QUOTE_MIN_FRAGMENT_CHARS = 4
_QUOTE_STRONG_BREAK_CHARS = "。！？!?；;\n"
_QUOTE_SOFT_BREAK_CHARS = "，,、"
_HOOK_MAX_CHARS = 20
_CAPTION_COLOR = (255, 250, 240)
_CLASSIC_TOP = (74, 60, 46)
_CLASSIC_BOTTOM = (34, 28, 22)
_END_PAPER = (244, 236, 222)
_END_INK = (64, 52, 40)
_END_MUTED = (120, 106, 88)
_END_CODE_SIZE = 240
_END_TAGLINE = "也为家里的长辈，留一本回忆录"


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
    # A sentence quoted verbatim from selected_text, shown before the narration.
    hook_text: str = ""
    book_title: str = ""
    narrator_name: str = ""
    # PNG mini program code that opens this video's playback page.
    mini_program_code: bytes | None = None


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


async def _stop_ffmpeg(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
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


async def _run_ffmpeg(args: list[str], *, frames: Iterable[bytes] | None = None) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if frames is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    readers = []
    try:
        if frames is None:
            _stdout, stderr = await process.communicate()
        else:
            # Drain encoder diagnostics while feeding frames so neither pipe
            # can stall the worker. Yield between frames to preserve its lease.
            readers = [asyncio.create_task(process.stdout.read()), asyncio.create_task(process.stderr.read())]
            try:
                for frame in frames:
                    process.stdin.write(frame)
                    await process.stdin.drain()
                    await asyncio.sleep(0)
            except (BrokenPipeError, ConnectionResetError):
                # A failed encoder's stderr below is the actionable error.
                pass
            finally:
                process.stdin.close()
            await process.wait()
            _stdout, stderr = await asyncio.gather(*readers)
    except BaseException:
        await _stop_ffmpeg(process)
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        raise
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:]
        raise ShareVideoGenerationError(message[0] if message else "ffmpeg 执行失败")


# The longest opening hook is 3 s, so this lands on the first story image in every video.
_CARD_SEEK_SECONDS = 3.5
_CARD_HEIGHT = round(_VIDEO_WIDTH * 4 / 5)
_CARD_TOP = round(_VIDEO_HEIGHT * _SUBJECT_CENTER_Y - _CARD_HEIGHT / 2)


async def _video_still(source_path: Path, suffix: str, seek_seconds: float, video_filter: str, quality: int) -> Path:
    still_path = source_path.with_suffix(suffix)
    if still_path.is_file() and still_path.stat().st_size:
        return still_path
    # Each job's video is immutable, so the still is extracted once and reused.
    with tempfile.TemporaryDirectory(prefix="still-", dir=source_path.parent) as directory:
        temporary_path = Path(directory) / "still.jpg"
        await asyncio.wait_for(_run_ffmpeg([
            # A negative seek counts back from the end of the video.
            _ffmpeg_executable(), "-y", "-sseof" if seek_seconds < 0 else "-ss", str(seek_seconds),
            "-i", str(source_path),
            "-frames:v", "1", "-vf", video_filter, "-q:v", str(quality), "-an",
            str(temporary_path),
        ]), timeout=10)
        if not temporary_path.is_file() or not temporary_path.stat().st_size:
            raise ShareVideoGenerationError("视频缩略图生成失败")
        temporary_path.replace(still_path)
    return still_path


async def share_video_thumbnail(source_path: Path) -> Path:
    # A small frame for lists, so home does not download the video.
    return await _video_still(source_path, ".thumbnail.jpg", 2, "scale=320:-2", 5)


async def share_video_poster(source_path: Path, hook_text: str = "") -> Path:
    """The portrait opening frame, with the opening line fully shown, as the player's poster."""
    # Videos without an opening line start on the story image straight away.
    seek_seconds = _hook_seconds(hook_text.strip()) - 0.5 if hook_text.strip() else 0.5
    return await _video_still(source_path, ".poster.jpg", seek_seconds, "null", 2)


async def share_video_end_frame(source_path: Path) -> Path:
    """The closing card with the mini program code, held on screen after playback."""
    return await _video_still(source_path, ".end.jpg", -0.3, "null", 2)


async def share_video_card_cover(source_path: Path, hook_text: str = "", title: str = "") -> Path:
    """A 5:4 forwarded cover with the opening quote, or the title when none exists.

    The card shows a 5:4 image, so a portrait frame gets centre-cropped and upscaled;
    cropping a full-width still here keeps the picture sharp and leaves out the
    watermark and moving captions, and the opening line is set once, whole.
    """
    # v2 replaces cached textless covers when a clip has no usable opening quote.
    cover_path = source_path.with_suffix(".card-v2.jpg")
    if cover_path.is_file() and cover_path.stat().st_size:
        return cover_path
    still_path = await _video_still(
        source_path,
        ".card-still.jpg",
        _CARD_SEEK_SECONDS,
        f"crop={_VIDEO_WIDTH}:{_CARD_HEIGHT}:0:{_CARD_TOP}",
        2,
    )
    with Image.open(still_path) as still:
        cover = still.convert("RGB")
    caption = hook_text.strip() or title.strip()
    if caption:
        _draw_card_text(cover, caption, quoted=bool(hook_text.strip()))
    temporary_path = cover_path.with_name(f".{cover_path.name}.{os.getpid()}.tmp")
    cover.save(temporary_path, "JPEG", quality=90)
    temporary_path.replace(cover_path)
    still_path.unlink(missing_ok=True)
    return cover_path


def _draw_card_text(cover: Image.Image, text: str, *, quoted: bool) -> None:
    lines = [text[start:end] for start, end in _quote_line_ranges(text, 0, len(text))]
    if quoted:
        lines[0] = f"“{lines[0]}"
        lines[-1] = f"{lines[-1]}”"
    font = _font(46, bold=True)
    line_height = 66
    text_top = cover.height - 44 - line_height * len(lines)
    # A soft dark fade behind the line keeps it readable on any picture.
    fade_top = max(0, text_top - 90)
    ys = np.linspace(0, 1, cover.height - fade_top, dtype=np.float32)[:, None]
    pixels = np.asarray(cover, np.float32)
    pixels[fade_top:] *= (1 - 0.6 * ys * ys * (3 - 2 * ys))[..., None]
    cover.paste(Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)))
    layer = _text_layer(lines, font, line_height=line_height, fill=(*_CAPTION_COLOR, 255), shadow=True)
    cover.paste(layer, (0, text_top - 24), layer)



def _subtitle_ranges(text: str, *, max_chars: int = 24) -> list[tuple[int, int]]:
    sentence_ranges = _quote_sentence_page_ranges(text)
    fragments: list[tuple[int, int, bool]] = []
    for sentence_start, sentence_end in sentence_ranges:
        pieces = _split_quote_range(text, sentence_start, sentence_end, max_chars=max_chars)
        fragments.extend((start, end, len(pieces) > 1) for start, end in pieces)
    pages: list[tuple[int, int]] = []
    page_start: int | None = None
    page_end: int | None = None
    page_sentences = 0
    page_is_split_sentence = False
    for fragment_start, fragment_end, is_split_sentence in fragments:
        fragment_sentences = max(
            1,
            sum(char in _QUOTE_STRONG_BREAK_CHARS for char in text[fragment_start:fragment_end]),
        )
        if page_start is not None and page_end is not None and (
            _visible_range_length(text, page_start, fragment_end) > max_chars
            or page_sentences + fragment_sentences > _QUOTE_PAGE_MAX_SENTENCES
            or _visible_range_length(text, page_start, page_end) > _QUOTE_LINE_MAX_CHARS
            or _visible_range_length(text, fragment_start, fragment_end) > _QUOTE_LINE_MAX_CHARS
            or page_is_split_sentence
            or is_split_sentence
        ):
            pages.append((page_start, page_end))
            page_start = None
            page_end = None
            page_sentences = 0
            page_is_split_sentence = False
        if page_start is None:
            page_start = fragment_start
        page_end = fragment_end
        page_sentences += fragment_sentences
        page_is_split_sentence = is_split_sentence
    if page_start is not None and page_end is not None:
        pages.append((page_start, page_end))
    return pages


def _visible_range_length(text: str, start: int, end: int) -> int:
    return sum(1 for char in text[start:end] if not char.isspace())


def _quote_sentence_page_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char in _QUOTE_STRONG_BREAK_CHARS:
            ranges.append((start, index + 1))
            start = index + 1
    if start < len(text):
        ranges.append((start, len(text)))
    sentences: list[tuple[int, int]] = []
    for start, end in ranges:
        start, end = _strip_range(text, start, end)
        if start < end:
            sentences.append((start, end))
    return sentences


def _split_quote_range(text: str, start: int, end: int, *, max_chars: int) -> list[tuple[int, int]]:
    visible_prefix = [0]
    for char in text[start:end]:
        visible_prefix.append(visible_prefix[-1] + int(not char.isspace()))

    def visible_count(left: int, right: int) -> int:
        return visible_prefix[right - start] - visible_prefix[left - start]

    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        remaining = visible_count(cursor, end)
        if remaining <= max_chars:
            ranges.append((cursor, end))
            break
        target_chars = round(remaining / ((remaining + max_chars - 1) // max_chars))
        viable = [
            index
            for index in range(cursor + 1, end)
            if _QUOTE_MIN_FRAGMENT_CHARS <= visible_count(cursor, index) <= max_chars
            and visible_count(index, end) >= _QUOTE_MIN_FRAGMENT_CHARS
            and text[index] not in _QUOTE_SOFT_BREAK_CHARS + _QUOTE_STRONG_BREAK_CHARS
        ]
        if not viable:
            ranges.append((cursor, end))
            break
        candidates = [
            index
            for index in viable
            if text[index - 1] in _QUOTE_SOFT_BREAK_CHARS
            and abs(visible_count(cursor, index) - target_chars) <= 4
        ]
        split = min(
            candidates or viable,
            key=lambda index: (abs(visible_count(cursor, index) - target_chars), index),
        )
        ranges.append((cursor, split))
        cursor = split
    return ranges


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
    visible_chars = _visible_range_length(text, start, end)
    if visible_chars <= 8 and "\n" not in text[start:end]:
        return [(start, end)]
    if visible_chars <= 16 and not any(
        char in _QUOTE_STRONG_BREAK_CHARS + _QUOTE_SOFT_BREAK_CHARS
        for char in text[start:end]
    ):
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
    options: list[tuple[tuple[int, int, int, int], list[tuple[int, int]]]] = []
    for split in range(start + 1, end):
        left = _strip_range(text, start, split)
        right = _strip_range(text, split, end)
        if left[0] >= left[1] or right[0] >= right[1]:
            continue
        if text[right[0]] in "，,、。！？!?；;":
            continue
        left_chars = _visible_range_length(text, *left)
        right_chars = _visible_range_length(text, *right)
        if min(left_chars, right_chars) < _QUOTE_MIN_FRAGMENT_CHARS:
            continue
        preceding = text[split - 1]
        semantic_break = (
            0 if preceding in _QUOTE_STRONG_BREAK_CHARS
            else 1 if preceding in _QUOTE_SOFT_BREAK_CHARS
            else 2 if text[right[0]] in "与和但而却"
            else 3
        )
        score = (
            max(0, left_chars - _QUOTE_LINE_MAX_CHARS)
            + max(0, right_chars - _QUOTE_LINE_MAX_CHARS),
            semantic_break,
            abs(left_chars - right_chars),
            split,
        )
        options.append((score, [left, right]))
    if options:
        return min(options, key=lambda option: option[0])[1]
    return [(start, end)]


def _linear_timings(text: str, duration_ms: int) -> list[dict[str, int]]:
    char_count = max(1, len(text))
    return [
        {
            "start_ms": round(duration_ms * index / char_count),
            "end_ms": round(duration_ms * (index + 1) / char_count),
        }
        for index, _char in enumerate(text)
    ]



def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _SHARE_VIDEO_FONT_DIR / (_SHARE_VIDEO_BOLD_FONT_FILE if bold else _SHARE_VIDEO_FONT_FILE)
    if not path.exists():
        raise ShareVideoGenerationError("分享视频中文字体文件缺失")
    return ImageFont.truetype(str(path), size)


def _ease(value: float) -> float:
    p = min(1.0, max(0.0, value))
    return p * p * (3 - 2 * p)


def _relative_timings(request: ShareVideoRenderRequest) -> list[dict[str, int]]:
    duration_ms = max(1, request.end_ms - request.start_ms)
    if len(request.char_timings) != len(request.selected_text):
        return _linear_timings(request.selected_text, duration_ms)
    return [
        {
            "start_ms": int(timing["start_ms"]) - request.start_ms,
            "end_ms": int(timing["end_ms"]) - request.start_ms,
        }
        for timing in request.char_timings
    ]


def _spoken_char_count(timings: list[dict[str, int]], start: int, end: int, t_ms: float) -> int:
    """Characters of text[start:end] whose speech has begun; pauses hold the count."""
    return sum(1 for index in range(start, end) if timings[index]["start_ms"] <= t_ms)


def usable_hook_text(hook_text: str, selected_text: str) -> str:
    hook = hook_text.strip()
    if not hook or hook not in selected_text:
        return ""
    return hook if _visible_range_length(hook, 0, len(hook)) <= _HOOK_MAX_CHARS else ""


def _hook_seconds(hook_text: str) -> float:
    if not hook_text:
        return 0.0
    return min(3.0, max(1.8, 1.2 + 0.08 * _visible_range_length(hook_text, 0, len(hook_text))))


def _with_alpha(layer: Image.Image, alpha: float) -> Image.Image:
    mask = layer.getchannel("A")
    return mask if alpha >= 1 else mask.point(lambda value: round(value * max(0.0, alpha)))


def _text_layer(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    *,
    line_height: int,
    fill: tuple[int, int, int, int],
    shadow: bool,
) -> Image.Image:
    padding = 24
    layer = Image.new("RGBA", (_VIDEO_WIDTH, padding * 2 + line_height * len(lines)), (0, 0, 0, 0))
    centers = [(_VIDEO_WIDTH / 2, padding + line_height * (index + 0.5)) for index in range(len(lines))]
    if shadow:
        draw = ImageDraw.Draw(layer)
        for line, (x, y) in zip(lines, centers):
            draw.text((x, y + 2), line, font=font, fill=(0, 0, 0, 170), anchor="mm")
        layer = layer.filter(ImageFilter.GaussianBlur(5))
    draw = ImageDraw.Draw(layer)
    for line, (x, y) in zip(lines, centers):
        draw.text((x, y), line, font=font, fill=fill, anchor="mm")
    return layer


@dataclass(frozen=True)
class _CaptionLine:
    start: int
    end: int
    top: int
    bottom: int
    # x of each character boundary in the line, so the spoken part can be lit.
    boundaries: tuple[float, ...]


@dataclass(frozen=True)
class _CaptionPage:
    start: int
    end: int
    show_ms: int
    hide_ms: int
    dim: Image.Image
    bright: Image.Image
    lines: tuple[_CaptionLine, ...]


_CAPTION_FONT_SIZE = 40
_CAPTION_LINE_HEIGHT = 58


def _caption_pages(text: str, timings: list[dict[str, int]], body_ms: int) -> list[_CaptionPage]:
    font = _font(_CAPTION_FONT_SIZE)
    planned: list[tuple[int, int, list[tuple[int, int]], int, int]] = []
    for start, end in _subtitle_ranges(text, max_chars=_QUOTE_PAGE_MAX_CHARS):
        line_ranges = _quote_line_ranges(text, start, end)
        if line_ranges:
            show_ms = max(0, timings[start]["start_ms"] - 150)
            planned.append((start, end, line_ranges, show_ms, timings[end - 1]["end_ms"] + 600))
    pages: list[_CaptionPage] = []
    for index, (start, end, line_ranges, show_ms, hide_ms) in enumerate(planned):
        # A page leaves as the next one arrives, so captions never stack.
        next_show = planned[index + 1][3] if index + 1 < len(planned) else body_ms
        hide_ms = max(show_ms + 1, min(hide_ms, next_show))
        line_texts = [text[line_start:line_end] for line_start, line_end in line_ranges]
        dim = _text_layer(line_texts, font, line_height=_CAPTION_LINE_HEIGHT, fill=(*_CAPTION_COLOR, 150), shadow=True)
        bright = _text_layer(line_texts, font, line_height=_CAPTION_LINE_HEIGHT, fill=(*_CAPTION_COLOR, 255), shadow=False)
        lines = []
        for line_index, (line_start, line_end) in enumerate(line_ranges):
            line = text[line_start:line_end]
            left = (_VIDEO_WIDTH - font.getlength(line)) / 2
            top = 24 + _CAPTION_LINE_HEIGHT * line_index
            lines.append(_CaptionLine(
                start=line_start,
                end=line_end,
                top=top,
                bottom=top + _CAPTION_LINE_HEIGHT,
                boundaries=tuple(left + font.getlength(line[:count]) for count in range(len(line) + 1)),
            ))
        pages.append(_CaptionPage(start, end, show_ms, hide_ms, dim, bright, tuple(lines)))
    return pages


def _draw_caption(frame: Image.Image, pages: list[_CaptionPage], timings: list[dict[str, int]], t_ms: float) -> None:
    for page in pages:
        if not page.show_ms <= t_ms < page.hide_ms:
            continue
        alpha = min(1.0, (t_ms - page.show_ms) / 250, (page.hide_ms - t_ms) / 250)
        rise = (1 - _ease((t_ms - page.show_ms) / 350)) * 12
        top = round(_VIDEO_HEIGHT * _SUBTITLE_CENTER_Y - page.dim.height / 2 + rise)
        frame.paste(page.dim, (0, top), _with_alpha(page.dim, alpha))
        for line in page.lines:
            spoken = _spoken_char_count(timings, line.start, line.end, t_ms)
            if not spoken:
                continue
            box = (0, line.top, math.ceil(line.boundaries[spoken]), line.bottom)
            lit = page.bright.crop(box)
            frame.paste(lit, (0, top + line.top), _with_alpha(lit, alpha))
        return


_MOVES = ("push_in", "pan_right", "pull_out", "tilt_up", "pan_left")


@dataclass(frozen=True)
class _Shot:
    canvas: Image.Image
    start: float
    end: float
    move: str


def _canvas_size() -> tuple[int, int]:
    return round(_VIDEO_WIDTH * _CANVAS_SCALE), round(_VIDEO_HEIGHT * _CANVAS_SCALE)


def _picture_canvas(path: Path) -> Image.Image:
    width, height = _canvas_size()
    try:
        with Image.open(path) as source:
            picture = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError) as exc:
        raise ShareVideoGenerationError("背景图读取失败") from exc
    if picture.width / picture.height <= 0.8:
        return ImageOps.fit(picture, (width, height), Image.LANCZOS, centering=(0.5, 0.4))
    # Landscape photos keep their whole frame over a blurred copy instead of
    # losing faces to a portrait crop.
    canvas = ImageOps.fit(picture, (width, height), Image.BILINEAR).filter(ImageFilter.GaussianBlur(28))
    canvas = Image.eval(canvas, lambda value: round(value * 0.55))
    scale = min(width / picture.width, height * 0.62 / picture.height)
    foreground = picture.resize((round(picture.width * scale), round(picture.height * scale)), Image.LANCZOS)
    canvas.paste(foreground, ((width - foreground.width) // 2, round(height * _SUBJECT_CENTER_Y - foreground.height / 2)))
    return canvas


def _classic_canvas() -> Image.Image:
    width, height = _canvas_size()
    ramp = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    top, bottom = np.array(_CLASSIC_TOP, np.float32), np.array(_CLASSIC_BOTTOM, np.float32)
    pixels = np.broadcast_to(top + (bottom - top) * ramp, (height, width, 3))
    return Image.fromarray(pixels.astype(np.uint8))


def _picture_shots(segments: list[tuple[Path | None, float, float]]) -> list[_Shot]:
    canvases: dict[Path | None, Image.Image] = {}
    shots: list[_Shot] = []
    for path, start, end in segments:
        if path not in canvases:
            canvases[path] = _picture_canvas(path) if path is not None else _classic_canvas()
        # Long scenes become several gentle shots of the same picture.
        count = max(1, round((end - start) / _SHOT_TARGET_SECONDS))
        for index in range(count):
            shots.append(_Shot(
                canvas=canvases[path],
                start=start + (end - start) * index / count,
                end=start + (end - start) * (index + 1) / count,
                move=_MOVES[len(shots) % len(_MOVES)],
            ))
    return shots


def _shot_frame(shot: _Shot, t: float) -> Image.Image:
    p = _ease((t - shot.start) / max(0.1, shot.end - shot.start))
    canvas_width, canvas_height = shot.canvas.size
    scale, cx, cy = 1.06, canvas_width / 2, canvas_height * 0.47
    if shot.move == "push_in":
        scale = 1.16 + (1.03 - 1.16) * p
    elif shot.move == "pull_out":
        scale = 1.03 + (1.16 - 1.03) * p
    box_width, box_height = _VIDEO_WIDTH * scale, _VIDEO_HEIGHT * scale
    x_room, y_room = (canvas_width - box_width) / 2, (canvas_height - box_height) / 2
    if shot.move == "pan_right":
        cx += x_room * 0.9 * (2 * p - 1)
    elif shot.move == "pan_left":
        cx -= x_room * 0.9 * (2 * p - 1)
    elif shot.move == "tilt_up":
        cy -= y_room * 0.9 * (2 * p - 1)
    left = min(max(0.0, cx - box_width / 2), canvas_width - box_width)
    top = min(max(0.0, cy - box_height / 2), canvas_height - box_height)
    return shot.canvas.resize(
        (_VIDEO_WIDTH, _VIDEO_HEIGHT), Image.BILINEAR, box=(left, top, left + box_width, top + box_height),
    )


def _shots_visual(shots: list[_Shot]) -> Callable[[float], Image.Image]:
    def visual(t: float) -> Image.Image:
        index = max([0, *(i for i, shot in enumerate(shots) if shot.start <= t)])
        frame = _shot_frame(shots[index], t)
        if index + 1 < len(shots):
            following = shots[index + 1]
            progress = (t - (following.start - _CROSSFADE_SECONDS)) / _CROSSFADE_SECONDS
            if progress > 0:
                frame = Image.blend(frame, _shot_frame(following, t), min(1.0, progress))
        return frame
    return visual


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


def _body_visual(request: ShareVideoRenderRequest, body_seconds: float) -> Callable[[float], Image.Image]:
    segments = _valid_background_segments(request)
    if len(segments) > 1:
        return _shots_visual(_picture_shots([
            (segment.image_path, segment.start_ms / 1000, segment.end_ms / 1000) for segment in segments
        ]))
    image_path = request.background_image_path or (segments[0].image_path if segments else None)
    if image_path is not None and not (image_path.exists() and image_path.stat().st_size > 0):
        image_path = None
    return _shots_visual(_picture_shots([(image_path, 0.0, body_seconds)]))


def _finishing_layers() -> tuple[np.ndarray, list[np.ndarray]]:
    """Warm grade, vignette and the darkened caption band, plus film grain."""
    ys, xs = np.mgrid[0:_VIDEO_HEIGHT, 0:_VIDEO_WIDTH].astype(np.float32)
    vignette = np.clip(
        1 - 0.5 * (((xs - _VIDEO_WIDTH / 2) / (_VIDEO_WIDTH * 0.75)) ** 2 + ((ys - _VIDEO_HEIGHT / 2) / (_VIDEO_HEIGHT * 0.7)) ** 2),
        0.4, 1,
    )
    band_progress = np.clip((ys / _VIDEO_HEIGHT - 0.52) / 0.34, 0, 1)
    band = 1 - 0.55 * band_progress * band_progress * (3 - 2 * band_progress)
    shade = (vignette * band)[..., None] * np.array([1.04, 1.0, 0.9], np.float32)
    rng = np.random.default_rng(7)
    grain = [rng.normal(0, 3, (_VIDEO_HEIGHT, _VIDEO_WIDTH, 1)).astype(np.float32) for _ in range(4)]
    return shade.astype(np.float32), grain


def _finish(frame: Image.Image | np.ndarray, shade: np.ndarray, grain: np.ndarray, brightness: float = 1.0) -> Image.Image:
    pixels = np.asarray(frame, np.float32) * shade + grain
    if brightness < 1:
        pixels *= max(0.0, brightness)
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def _hook_layer(hook_text: str) -> Image.Image:
    lines = [hook_text[start:end] for start, end in _quote_line_ranges(hook_text, 0, len(hook_text))]
    return _text_layer(lines, _font(52, bold=True), line_height=78, fill=(*_CAPTION_COLOR, 255), shadow=True)


def _fit_font(text: str, size: int, max_width: int, *, bold: bool) -> ImageFont.FreeTypeFont:
    font = _font(size, bold=bold)
    while size > 28 and font.getlength(text) > max_width:
        size -= 4
        font = _font(size, bold=bold)
    return font


def _end_card(book_title: str, narrator_name: str, mini_program_code: bytes | None = None) -> Image.Image:
    ys, xs = np.mgrid[0:_VIDEO_HEIGHT, 0:_VIDEO_WIDTH].astype(np.float32)
    falloff = np.clip(1 - 0.22 * (((xs - _VIDEO_WIDTH / 2) / _VIDEO_WIDTH) ** 2 + ((ys - _VIDEO_HEIGHT * 0.45) / _VIDEO_HEIGHT) ** 2) * 2, 0.8, 1)
    card = Image.fromarray((falloff[..., None] * np.array(_END_PAPER, np.float32)).astype(np.uint8)).convert("RGBA")
    draw = ImageDraw.Draw(card)
    center = _VIDEO_WIDTH / 2
    code = None
    if mini_program_code:
        try:
            code = Image.open(io.BytesIO(mini_program_code)).convert("RGBA")
            code.thumbnail((_END_CODE_SIZE, _END_CODE_SIZE), Image.LANCZOS)
        except (OSError, ValueError):
            code = None
    # The code takes the middle of the card, so the text block moves up.
    top = 300 if code else 380
    clean_title = "".join(char for char in book_title.strip().strip("《》") if char.isprintable())[:18]
    if clean_title:
        draw.text((center, top), "这段故事，出自", font=_font(30), fill=_END_MUTED, anchor="mm")
        title = f"《{clean_title}》"
        draw.text((center, top + 82), title, font=_fit_font(title, 60, 640, bold=True), fill=_END_INK, anchor="mm")
    else:
        draw.text((center, top + 60), "一段家庭口述", font=_font(44, bold=True), fill=_END_INK, anchor="mm")
    clean_narrator = "".join(char for char in narrator_name.strip() if char.isprintable())[:12]
    if clean_narrator:
        draw.text((center, top + 160), f"讲述人 · {clean_narrator}", font=_font(30), fill=_END_MUTED, anchor="mm")
    lockup_top = 760
    if code:
        code_top = top + 240
        card.alpha_composite(code, (round(center - code.width / 2), code_top))
        draw.text((center, code_top + code.height + 36), "微信扫码，打开这段讲述", font=_font(24), fill=_END_MUTED, anchor="mm")
        lockup_top = code_top + code.height + 100
    lockup = render_brand_lockup(300, variant="dark")
    card.alpha_composite(lockup, (round(center - lockup.width / 2), lockup_top))
    draw.text((center, lockup_top + lockup.height + 48), _END_TAGLINE, font=_font(24), fill=_END_MUTED, anchor="mm")
    return card.convert("RGB")


def _video_frames(
    request: ShareVideoRenderRequest,
    *,
    hook_text: str,
    hook_seconds: float,
    body_seconds: float,
) -> Iterator[bytes]:
    visual = _body_visual(request, body_seconds)
    timings = _relative_timings(request)
    pages = _caption_pages(request.selected_text, timings, round(body_seconds * 1000))
    shade, grain = _finishing_layers()
    watermark = render_brand_lockup(150, variant="light")
    watermark_mask = _with_alpha(watermark, 0.6)
    end_card = np.asarray(_end_card(request.book_title, request.narrator_name, request.mini_program_code), np.float32)
    total_seconds = hook_seconds + body_seconds + _END_CARD_SECONDS
    if hook_seconds:
        opening = np.asarray(visual(0.0), np.float32)
        hook_backdrop = np.asarray(Image.fromarray(opening.astype(np.uint8)).filter(ImageFilter.GaussianBlur(10)), np.float32) * 0.6
        hook_layer = _hook_layer(hook_text)
        hook_top = round(_VIDEO_HEIGHT * _SUBJECT_CENTER_Y - hook_layer.height / 2)
    for index in range(math.ceil(total_seconds * _FPS)):
        t = index / _FPS
        noise = grain[index % len(grain)]
        if t < hook_seconds:
            # The hook settles into the first shot as its sentence fades out.
            settle = (t - (hook_seconds - 0.4)) / 0.4
            pixels = hook_backdrop if settle <= 0 else hook_backdrop + (opening - hook_backdrop) * min(1.0, settle)
            frame = _finish(pixels, shade, noise, brightness=min(1.0, t / 0.3))
            alpha = min(1.0, (t - 0.15) / 0.35, (hook_seconds - 0.1 - t) / 0.35)
            if alpha > 0:
                frame.paste(hook_layer, (0, hook_top), _with_alpha(hook_layer, alpha))
        elif t < hook_seconds + body_seconds:
            body_t = t - hook_seconds
            fade_in = 1.0 if hook_seconds else min(1.0, body_t / 0.4)
            frame = _finish(visual(body_t), shade, noise, brightness=min(fade_in, (body_seconds - body_t) / 0.4))
            frame.paste(watermark, (28, 44), watermark_mask)
            _draw_caption(frame, pages, timings, body_t * 1000)
        else:
            end_t = t - hook_seconds - body_seconds
            frame = Image.fromarray(np.clip(end_card * min(1.0, end_t / 0.5) + noise, 0, 255).astype(np.uint8))
        yield frame.tobytes()


async def _prepare_audio(
    ffmpeg: str,
    request: ShareVideoRenderRequest,
    target_path: Path,
    *,
    delay_seconds: float,
    total_seconds: float,
) -> None:
    input_args = ["-f", "s16le", "-ar", "16000", "-ac", "1"] if request.source_audio_mime_type == "audio/pcm" else []
    body_seconds = max(0.1, (request.end_ms - request.start_ms) / 1000)
    await _run_ffmpeg([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        *input_args, "-i", str(request.source_audio_path),
        "-af", (
            f"atrim=start={request.start_ms / 1000:.3f}:duration={body_seconds:.3f},asetpts=PTS-STARTPTS,aformat=channel_layouts=mono,"
            # Light clean-up keeps the original voice while evening out phone recordings.
            "highpass=f=70,afftdn,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
            f"adelay={round(delay_seconds * 1000)},apad=whole_dur={total_seconds:.3f}"
        ),
        "-t", f"{total_seconds:.3f}", "-ac", "1", "-c:a", "pcm_s16le", str(target_path),
    ])


async def render_share_video_clip(request: ShareVideoRenderRequest) -> None:
    ffmpeg = _ffmpeg_executable()
    request.target_path.parent.mkdir(parents=True, exist_ok=True)
    body_seconds = max(0.1, (request.end_ms - request.start_ms) / 1000)
    hook_text = usable_hook_text(request.hook_text, request.selected_text)
    hook_seconds = _hook_seconds(hook_text)
    total_seconds = hook_seconds + body_seconds + _END_CARD_SECONDS
    frames = _video_frames(request, hook_text=hook_text, hook_seconds=hook_seconds, body_seconds=body_seconds)
    with tempfile.TemporaryDirectory(prefix="memoir-share-video-") as tmp:
        tmpdir = Path(tmp)
        audio_path = tmpdir / "audio.wav"
        temp_video_path = tmpdir / "video.mp4"
        await _prepare_audio(ffmpeg, request, audio_path, delay_seconds=hook_seconds, total_seconds=total_seconds)
        await _run_ffmpeg([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{_VIDEO_WIDTH}x{_VIDEO_HEIGHT}",
            "-r", str(_FPS), "-i", "-", "-i", str(audio_path),
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-t", f"{total_seconds:.3f}", "-movflags", "+faststart", str(temp_video_path),
        ], frames=frames)
        temp_video_path.replace(request.target_path)
