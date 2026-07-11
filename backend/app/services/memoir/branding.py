from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat


BRAND_NAME = "余主任留声机"
BRAND_ATTRIBUTION = f"由{BRAND_NAME}协助整理"
BRAND_VERSION = "memoir-brand-v1"

_APP_DIR = Path(__file__).resolve().parents[2]
BRAND_MARK_PATH = _APP_DIR / "assets" / "branding" / "memoir-gramophone.png"
BRAND_FONT_PATH = _APP_DIR / "assets" / "fonts" / "NotoSerifSC-Bold.ttf"

_DARK_COLOR = (58, 33, 25, 255)
_LIGHT_COLOR = (255, 250, 244, 255)
_MIN_BRANDABLE_WIDTH = 320
_MIN_BRANDABLE_HEIGHT = 180


@lru_cache(maxsize=1)
def _brand_mark() -> Image.Image:
    with Image.open(BRAND_MARK_PATH) as source:
        mark = source.convert("RGBA")
    bounds = mark.getbbox()
    return mark.crop(bounds) if bounds else mark


def render_brand_lockup(
    max_width: int,
    *,
    variant: Literal["dark", "light"] = "dark",
    opacity: float = 1.0,
) -> Image.Image:
    """Render the established gramophone mark with an exact Chinese wordmark."""
    max_width = max(80, int(max_width))
    opacity = max(0.0, min(1.0, opacity))
    color = _LIGHT_COLOR if variant == "light" else _DARK_COLOR

    font_size = max(12, int(max_width * 0.105))
    font = ImageFont.truetype(str(BRAND_FONT_PATH), font_size)
    probe = Image.new("RGBA", (1, 1))
    probe_draw = ImageDraw.Draw(probe)
    text_bounds = probe_draw.textbbox((0, 0), BRAND_NAME, font=font, stroke_width=1)
    text_width = text_bounds[2] - text_bounds[0]
    text_height = text_bounds[3] - text_bounds[1]

    mark_source = _brand_mark()
    mark_height = max(text_height + 8, int(font_size * 1.75))
    mark_width = max(1, round(mark_source.width * mark_height / mark_source.height))
    gap = max(6, round(font_size * 0.38))
    total_width = mark_width + gap + text_width
    if total_width > max_width:
        scale = max_width / total_width
        font_size = max(10, round(font_size * scale))
        font = ImageFont.truetype(str(BRAND_FONT_PATH), font_size)
        text_bounds = probe_draw.textbbox((0, 0), BRAND_NAME, font=font, stroke_width=1)
        text_width = text_bounds[2] - text_bounds[0]
        text_height = text_bounds[3] - text_bounds[1]
        mark_height = max(text_height + 6, round(mark_height * scale))
        mark_width = max(1, round(mark_source.width * mark_height / mark_source.height))
        gap = max(5, round(gap * scale))
        total_width = mark_width + gap + text_width

    canvas_height = max(mark_height, text_height) + 4
    canvas = Image.new("RGBA", (total_width, canvas_height), (0, 0, 0, 0))
    mark = mark_source.resize((mark_width, mark_height), Image.Resampling.LANCZOS)
    if variant == "light":
        light_mark = Image.new("RGBA", mark.size, color)
        light_mark.putalpha(mark.getchannel("A"))
        mark = light_mark
    canvas.alpha_composite(mark, (0, (canvas_height - mark_height) // 2))

    draw = ImageDraw.Draw(canvas)
    text_x = mark_width + gap
    text_y = (canvas_height - text_height) // 2 - text_bounds[1]
    draw.text(
        (text_x, text_y),
        BRAND_NAME,
        font=font,
        fill=color,
        stroke_width=1,
        stroke_fill=color,
    )
    if opacity < 1.0:
        alpha = canvas.getchannel("A").point(lambda value: round(value * opacity))
        canvas.putalpha(alpha)
    return canvas


def _brand_variant(image: Image.Image, *, logo_width: int, margin: int) -> Literal["dark", "light"]:
    sample_width = min(image.width, logo_width + margin * 2)
    sample_height = min(image.height, max(round(logo_width * 0.35), margin * 2))
    region = image.crop(
        (
            image.width - sample_width,
            image.height - sample_height,
            image.width,
            image.height,
        )
    ).convert("L")
    return "light" if ImageStat.Stat(region).mean[0] < 145 else "dark"


def _derivative_path(source_path: Path, image_format: str | None) -> Path:
    suffix = source_path.suffix.lower()
    if image_format not in {"JPEG", "PNG", "WEBP"}:
        suffix = ".png"
    return source_path.with_name(f"{source_path.stem}.{BRAND_VERSION}{suffix}")


def branded_image_derivative(source_path: Path) -> Path:
    """Return a cached branded derivative without modifying the clean source image."""
    with Image.open(source_path) as source:
        image_format = source.format
        width, height = source.size

    if width < _MIN_BRANDABLE_WIDTH or height < _MIN_BRANDABLE_HEIGHT:
        return source_path

    target_path = _derivative_path(source_path, image_format)
    newest_input_mtime = max(source_path.stat().st_mtime, BRAND_MARK_PATH.stat().st_mtime)
    if target_path.exists() and target_path.stat().st_mtime >= newest_input_mtime:
        return target_path

    with Image.open(source_path) as source:
        base = ImageOps.exif_transpose(source).convert("RGBA")

    logo_width = min(300, max(140, round(base.width * 0.12)))
    margin = max(14, round(min(base.size) * 0.03))
    logo = render_brand_lockup(
        logo_width,
        variant=_brand_variant(base, logo_width=logo_width, margin=margin),
        opacity=0.72,
    )
    base.alpha_composite(
        logo,
        (base.width - logo.width - margin, base.height - logo.height - margin),
    )

    temporary_path = target_path.with_name(f".{target_path.name}.{os.getpid()}.tmp")
    save_format = image_format if image_format in {"JPEG", "PNG", "WEBP"} else "PNG"
    output = base.convert("RGB") if save_format == "JPEG" else base
    save_options: dict[str, object] = {}
    if save_format in {"JPEG", "WEBP"}:
        save_options["quality"] = 92
    if save_format == "PNG":
        save_options["optimize"] = True
    try:
        output.save(temporary_path, format=save_format, **save_options)
        os.replace(temporary_path, target_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target_path
