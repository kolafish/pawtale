"""Deterministic 5:4 reading cards; only explicitly selected text is rendered."""
from io import BytesIO
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ASSETS = Path(__file__).resolve().parents[2] / "assets"
_INK = "#614b39"


def _text(image: Image.Image, text: str, box: tuple[int, int, int, int], *, size: int, minimum: int = 22, highlight: bool = False, align: str = "center", bold: bool = False) -> None:
    """Wrap by measured glyph width, shrinking before truncating very long titles."""
    left, top, right, bottom = box
    draw = ImageDraw.Draw(image)
    text = " ".join(text.split())
    for font_size in range(size, minimum - 1, -2):
        font = ImageFont.truetype(str(_ASSETS / "fonts" / ("NotoSerifSC-Bold.ttf" if bold else "NotoSerifSC-Regular.ttf")), font_size)
        lines: list[str] = []
        line = ""
        for char in text:
            if line and draw.textlength(line + char, font=font) > right - left:
                lines.append(line)
                line = ""
            line += char
        if line:
            lines.append(line)
        height = round(font_size * 1.55)
        if len(lines) * height <= bottom - top:
            break
    limit = max(1, (bottom - top) // height)
    if len(lines) > limit:
        lines = lines[:limit]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > right - left:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    y = top + (bottom - top - len(lines) * height) / 2
    for index, line in enumerate(lines):
        x = left if align == "left" else (left + right - draw.textlength(line, font=font)) / 2
        if highlight and index == len(lines) - 1:
            draw.rounded_rectangle((x - 4, y - 3, x + draw.textlength(line, font=font) + 4, y + font_size + 7), radius=6, fill="#eaf0d3")
        draw.text((x, y), line, font=font, fill=_INK, anchor="lt")
        y += height


@lru_cache(maxsize=64)
def render_reading_share_card(*, title: str, book_title: str, is_book: bool,
                              chapter_count: int = 0, char_count: int = 0, excerpt: str = "") -> bytes:
    with Image.open(_ASSETS / "reading-share" / ("book.jpg" if is_book else "chapter.jpg")) as source:
        image = source.convert("RGB")
    if is_book:
        _text(image, title, (325, 165, 690, 350), size=48)
        footer = f"{chapter_count}章 · {char_count}字"
    else:
        _text(image, title, (105, 205, 450, 420), size=42)
        if excerpt:
            _text(image, f"“{excerpt}”", (550, 190, 890, 525), size=32, minimum=28)
        footer = f"选自《{book_title}》"
    _text(image, footer, (80, 735, 920, 790), size=28)
    output = BytesIO()
    image.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue()


@lru_cache(maxsize=64)
def render_excerpt_share_card(*, book_title: str, chapter_title: str, content: str) -> bytes:
    image = Image.new("RGB", (1000, 800), "#fcf8f1")
    with Image.open(_ASSETS / "branding/memoir-gramophone.png") as source:
        brand = source.convert("RGBA")
        brand.thumbnail((88, 88))
        image.paste(brand, (64, 44), brand)
    _text(image, "老余口述", (172, 44, 900, 88), size=30, align="left")
    _text(image, "一个家庭的回忆录", (172, 92, 900, 132), size=24, align="left")
    _text(image, chapter_title, (68, 164, 932, 300), size=52, minimum=34, align="left", bold=True)
    # The immutable snapshot stays complete; only the card's display is shortened.
    compact = " ".join(content.split())
    preview = compact[:80] + ("…" if len(compact) > 80 else "")
    _text(image, preview, (68, 324, 932, 652), size=40, minimum=38, highlight=True, align="left")
    ImageDraw.Draw(image).line((68, 698, 932, 698), fill="#e3d8c7", width=2)
    _text(image, f"选自《{book_title}》 · {chapter_title}", (68, 719, 932, 779), size=25, minimum=22, align="left")
    output = BytesIO()
    image.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue()
