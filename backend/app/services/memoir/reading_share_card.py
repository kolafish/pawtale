"""Deterministic 5:4 reading cards from author-confirmed share content."""
from io import BytesIO
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

_ASSETS = Path(__file__).resolve().parents[2] / "assets"
_INK = "#614b39"


def _text(image: Image.Image, text: str, box: tuple[int, int, int, int], *, size: int, minimum: int = 22, align: str = "center", bold: bool = False) -> None:
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
    for line in lines:
        x = left if align == "left" else (left + right - draw.textlength(line, font=font)) / 2
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


def render_excerpt_share_card(*, book_title: str, chapter_title: str, content: str,
                              summary: str = "", image_content: bytes | None = None, ai_image: bool = False) -> bytes:
    image = Image.new("RGB", (1000, 800), "#fcf8f1")
    _text(image, "老余口述 · 一个家庭的回忆录", (56, 24, 944, 80), size=32, align="left")
    # Older snapshots have no confirmed summary; retain a bounded original-text teaser.
    compact = " ".join(content.split())
    limit = min(64, max(1, len(compact) - 12)) if len(compact) > 24 else len(compact)
    preview = summary or compact[:limit].rstrip("，。！？；：、,.;!?: ") + ("…" if limit < len(compact) else "")
    if image_content:
        with Image.open(BytesIO(image_content)) as source:
            picture = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), (1000, 396))
        image.paste(picture, (0, 96))
        if ai_image:
            ImageDraw.Draw(image).rounded_rectangle((754, 438, 978, 482), radius=8, fill="#fcf8f1")
            _text(image, "AI生成插图", (766, 442, 968, 478), size=23)
        _text(image, preview, (56, 506, 944, 634), size=40, minimum=40, align="left")
    else:
        _text(image, preview, (64, 154, 936, 592), size=58, minimum=48, align="left")
    ImageDraw.Draw(image).line((56, 642, 944, 642), fill="#d5c5b3", width=2)
    _text(image, f"选自《{book_title}》", (56, 650, 944, 726), size=48, minimum=48, align="left")
    _text(image, chapter_title, (56, 736, 944, 790), size=32, minimum=32, align="left")
    output = BytesIO()
    image.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue()
