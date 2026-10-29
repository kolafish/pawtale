"""Deterministic 5:4 reading cards from author-confirmed share content."""
from io import BytesIO
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

_ASSETS = Path(__file__).resolve().parents[2] / "assets"
_INK = "#614b39"


def _wrapped_lines(text: str, font: ImageFont.FreeTypeFont, width: int, *, book_typography: bool = False) -> list[str]:
    if book_typography:
        from app.services.memoir.book_output_pdf_renderer import LINE_START_FORBIDDEN_CHARS, LINE_END_FORBIDDEN_CHARS

    lines: list[str] = []
    line = ""
    for char in " ".join(text.split()):
        if line and font.getlength(line + char) > width:
            next_line = char
            if book_typography:
                while len(line) > 1 and (next_line[0] in LINE_START_FORBIDDEN_CHARS or line[-1] in LINE_END_FORBIDDEN_CHARS):
                    next_line = line[-1] + next_line
                    line = line[:-1]
            lines.append(line)
            line = next_line
            continue
        line += char
    if line:
        lines.append(line)
    return lines


def _text(image: Image.Image, text: str, box: tuple[int, int, int, int], *, size: int, minimum: int = 22, align: str = "center", bold: bool = False, line_spacing: float = 1.55, color: str = _INK, book_typography: bool = False) -> None:
    """Wrap by measured glyph width, shrinking before truncating very long titles."""
    left, top, right, bottom = box
    draw = ImageDraw.Draw(image)
    for font_size in range(size, minimum - 1, -2):
        font = ImageFont.truetype(str(_ASSETS / "fonts" / ("NotoSerifSC-Bold.ttf" if bold else "NotoSerifSC-Regular.ttf")), font_size)
        lines = _wrapped_lines(text, font, right - left, book_typography=book_typography)
        height = round(font_size * line_spacing)
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
        draw.text((x, y), line, font=font, fill=color, anchor="lt")
        y += height


def _caption_height(text: str, *, width: int) -> int:
    if not text:
        return 0
    font = ImageFont.truetype(str(_ASSETS / "fonts" / "NotoSerifSC-Regular.ttf"), 22)
    return len(_wrapped_lines(text, font, width)) * 34 + 8


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
                              summary: str = "", image_content: bytes | None = None, ai_image: bool = False, caption: str = "") -> bytes:
    image = Image.new("RGB", (1000, 800), "#fcf8f1")
    _text(image, "老余口述 · 一个家庭的回忆录", (56, 24, 944, 80), size=32, align="left")
    # Older snapshots have no confirmed summary; retain a bounded original-text teaser.
    compact = " ".join(content.split())
    limit = min(64, max(1, len(compact) - 12)) if len(compact) > 24 else len(compact)
    preview = summary or compact[:limit].rstrip("，。！？；：、,.;!?: ") + ("…" if limit < len(compact) else "")
    if image_content:
        label = "AI 意象插图" if ai_image else " ".join(caption.split())
        caption_height = min(300, _caption_height(label, width=888))
        with Image.open(BytesIO(image_content)) as source:
            picture = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), (1000, max(96, 396 - caption_height)))
        image.paste(picture, (0, 96))
        if label:
            _text(image, label, (56, 492 - caption_height, 944, 492), size=22)
        _text(image, preview, (56, 506, 944, 634), size=40, minimum=40, align="left")
    else:
        _text(image, preview, (64, 154, 936, 592), size=58, minimum=48, align="left")
    ImageDraw.Draw(image).line((56, 642, 944, 642), fill="#d5c5b3", width=2)
    _text(image, f"选自《{book_title}》", (56, 650, 944, 726), size=48, minimum=48, align="left")
    _text(image, chapter_title, (56, 736, 944, 790), size=32, minimum=32, align="left")
    output = BytesIO()
    image.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue()


def render_excerpt_share_poster(*, title: str, book_title: str, quote: str, code_content: bytes,
                                image_content: bytes | None = None, ai_image: bool = False, caption: str = "") -> bytes:
    """A story print that grows for long quotes, keeping the type and code readable."""
    from PIL import ImageChops

    paper = "#fcf8f1"
    quote_top = 930 if image_content else 490
    quote_size = 48 if image_content else 66
    quote_spacing = 1.65
    quote_font = ImageFont.truetype(str(_ASSETS / "fonts" / "NotoSerifSC-Regular.ttf"), quote_size)
    quote_height = len(_wrapped_lines(quote, quote_font, 984, book_typography=True)) * round(quote_size * quote_spacing)
    # Grow the paper instead of shrinking or truncating the author's excerpt.
    quote_bottom = max(1200, quote_top + quote_height + 48)
    footer_top = quote_bottom + 20
    image = Image.new("RGB", (1200, footer_top + 380), paper)
    draw = ImageDraw.Draw(image)
    with Image.open(_ASSETS / "branding" / "laoyu-portrait.png") as source:
        mark = source.convert("RGBA")
        mark = ImageOps.contain(mark.crop(mark.getbbox()), (104, 116))
    image.paste(mark, (80, 44), mark)
    _text(image, "老余口述", (210, 40, 500, 116), size=46, minimum=46, align="left", bold=True)
    _text(image, "把家人的故事，慢慢写成一本书", (210, 116, 1120, 158), size=25, align="left")
    _text(image, title, (80, 187, 1120, 368), size=76, minimum=44, align="left", bold=True)
    draw.line((82, 388, 172, 388), fill="#b9785c", width=4)

    if image_content:
        label = "AI 意象插图" if ai_image else " ".join(caption.split())
        caption_height = _caption_height(label, width=1040)
        picture_height = 520 - caption_height
        with Image.open(BytesIO(image_content)) as source:
            picture = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGBA"), (1120, picture_height))
        # Fade only the outer edge of illustrations into the same paper used throughout.
        # Keep photos intact, including their original colors and full framing.
        if ai_image:
            fade = min(48, picture.width // 5, picture.height // 5)
            mask = Image.new("L", picture.size, 255)
            edge = ImageDraw.Draw(mask)
            for inset in range(fade):
                edge.rectangle((inset, inset, picture.width - 1 - inset, picture.height - 1 - inset),
                               outline=round(255 * inset / fade))
            picture.putalpha(ImageChops.multiply(picture.getchannel("A"), mask))
        image.paste(picture, ((1200 - picture.width) // 2, 410 + (picture_height - picture.height) // 2), picture)
        if label:
            _text(image, label, (80, 930 - caption_height, 1120, 930), size=22)
    _text(image, quote, (108, quote_top, 1092, quote_bottom), size=quote_size,
          minimum=quote_size, align="left", line_spacing=quote_spacing, book_typography=True)
    draw.line((80, footer_top, 1120, footer_top), fill="#decfbd", width=1)
    _text(image, f"选自《{book_title}》", (80, footer_top + 10, 1120, footer_top + 72), size=28, minimum=22)
    # A quiet centered paper panel; never cover or redraw the official code/avatar.
    draw.rounded_rectangle((480, footer_top + 80, 720, footer_top + 320), radius=26, fill="#f3ebdf")
    with Image.open(BytesIO(code_content)) as source:
        code = ImageOps.contain(source.convert("RGBA"), (220, 220))
    image.paste(code, ((1200 - code.width) // 2, footer_top + 90 + (220 - code.height) // 2), code)
    _text(image, "微信扫码 · 阅读这段回忆", (80, footer_top + 324, 1120, footer_top + 376), size=28, minimum=28, color="#49382b")
    output = BytesIO()
    image.save(output, "JPEG", quality=94, optimize=True)
    return output.getvalue()
