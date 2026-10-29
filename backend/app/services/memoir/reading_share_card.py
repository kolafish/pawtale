"""Deterministic 5:4 reading cards from author-confirmed share content."""
from io import BytesIO
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

_ASSETS = Path(__file__).resolve().parents[2] / "assets"
_INK = "#614b39"
_PAPER = (252, 248, 241)


def _excerpt_picture(content: bytes | None) -> tuple[Image.Image | None, tuple[int, int, int]]:
    """Use the dominant edge color, rather than people's skin or clothes, as paper tint."""
    if not content:
        return None, _PAPER
    with Image.open(BytesIO(content)) as source:
        picture = ImageOps.exif_transpose(source).convert("RGBA")
    sample = picture.resize((64, 64))
    opaque = Image.new("RGBA", sample.size, (*_PAPER, 255))
    opaque.alpha_composite(sample)
    border = Image.new("RGB", (64, 24))
    # Sample all four edges, excluding the subject in the middle.
    for index, box in enumerate(((0, 0, 64, 6), (0, 58, 64, 64), (0, 6, 6, 58), (58, 6, 64, 58))):
        border.paste(opaque.crop(box).convert("RGB").resize((64, 6)), (0, index * 6))
    palette = border.quantize(colors=8).convert("RGB")
    _, tint = max(palette.getcolors(64 * 24), key=lambda item: item[0])
    # Keep even dark/saturated photos light enough for the existing brown text.
    paper = tuple(round(255 * .88 + color * .12) for color in tint)
    return picture, paper


def _paste_excerpt_picture(image: Image.Image, picture: Image.Image, position: tuple[int, int]) -> None:
    """Blend a narrow edge for both photos and illustrations; leave the center intact."""
    fade = max(1, min(32, picture.width // 25, picture.height // 25))
    horizontal = Image.new("L", picture.size, 255)
    vertical = Image.new("L", picture.size, 255)
    x_edge, y_edge = ImageDraw.Draw(horizontal), ImageDraw.Draw(vertical)
    for inset in range(fade):
        progress = inset / fade
        alpha = round(255 * progress * progress * (3 - 2 * progress))
        x_edge.line((inset, 0, inset, picture.height), fill=alpha)
        x_edge.line((picture.width - 1 - inset, 0, picture.width - 1 - inset, picture.height), fill=alpha)
        y_edge.line((0, inset, picture.width, inset), fill=alpha)
        y_edge.line((0, picture.height - 1 - inset, picture.width, picture.height - 1 - inset), fill=alpha)
    mask = ImageChops.multiply(horizontal, vertical)
    image.paste(picture, position, ImageChops.multiply(picture.getchannel("A"), mask))


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
    picture, paper = _excerpt_picture(image_content)
    image = Image.new("RGB", (1000, 800), paper)
    _text(image, "老余口述 · 一个家庭的回忆录", (56, 24, 944, 80), size=32, align="left")
    # Older snapshots have no confirmed summary; retain a bounded original-text teaser.
    compact = " ".join(content.split())
    limit = min(64, max(1, len(compact) - 12)) if len(compact) > 24 else len(compact)
    preview = summary or compact[:limit].rstrip("，。！？；：、,.;!?: ") + ("…" if limit < len(compact) else "")
    if picture is not None:
        label = "AI 意象插图" if ai_image else " ".join(caption.split())
        caption_height = min(300, _caption_height(label, width=888))
        picture = ImageOps.fit(picture, (1000, max(96, 396 - caption_height)))
        _paste_excerpt_picture(image, picture, (0, 96))
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
    picture, paper = _excerpt_picture(image_content)
    label = "AI 意象插图" if ai_image else " ".join(caption.split())
    caption_height = _caption_height(label, width=1040) if picture is not None else 0
    if picture is not None:
        picture = ImageOps.contain(picture, (1040, 780))
    quote_top = 410 + picture.height + caption_height + 24 if picture is not None else 490
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

    if picture is not None:
        _paste_excerpt_picture(image, picture, ((1200 - picture.width) // 2, 410))
        if label:
            _text(image, label, (80, 410 + picture.height, 1120, quote_top - 24), size=22)
    _text(image, quote, (108, quote_top, 1092, quote_bottom), size=quote_size,
          minimum=quote_size, align="left", line_spacing=quote_spacing, book_typography=True)
    draw.line((80, footer_top, 1120, footer_top), fill="#decfbd", width=1)
    _text(image, f"选自《{book_title}》", (80, footer_top + 10, 1120, footer_top + 72), size=28, minimum=22)
    # A quiet centered paper panel; never cover or redraw the official code/avatar.
    draw.rounded_rectangle((480, footer_top + 80, 720, footer_top + 320), radius=26,
                           fill=tuple(channel - 8 for channel in paper))
    with Image.open(BytesIO(code_content)) as source:
        code = ImageOps.contain(source.convert("RGBA"), (220, 220))
    image.paste(code, ((1200 - code.width) // 2, footer_top + 90 + (220 - code.height) // 2), code)
    _text(image, "微信扫码 · 阅读这段回忆", (80, footer_top + 324, 1120, footer_top + 376), size=28, minimum=28, color="#49382b")
    output = BytesIO()
    image.save(output, "JPEG", quality=94, optimize=True)
    return output.getvalue()
