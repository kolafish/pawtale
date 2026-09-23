from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from threading import Lock

from PIL import Image, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.memoir.branding import BRAND_NAME

PDF_SANS_FONT_NAME = "MemoirNotoSansSC"
PDF_SERIF_FONT_NAME = "MemoirNotoSerifSC"
PDF_SERIF_BOLD_FONT_NAME = "MemoirNotoSerifSCBold"
PDF_FONT_NAME = PDF_SERIF_FONT_NAME
PDF_SANS_FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "NotoSansSC-Regular.ttf"
)
PDF_SERIF_FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "NotoSerifSC-Regular.ttf"
)
PDF_SERIF_BOLD_FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "NotoSerifSC-Bold.ttf"
)
_PDF_FONT_REGISTERED = False
_PDF_FONT_REGISTER_LOCK = Lock()
PDF_EMBEDDED_IMAGE_DPI = 300
PDF_EMBEDDED_IMAGE_JPEG_QUALITY = 90
PDFColor = tuple[float, float, float]
PDF_BODY_TEXT: PDFColor = (0.08, 0.07, 0.06)
PDF_HEADER_TEXT: PDFColor = (0.58, 0.55, 0.51)
PDF_HERITAGE_ACCENT: PDFColor = (0.47, 0.35, 0.25)
PDF_WARM_ACCENT: PDFColor = (0.56, 0.34, 0.25)
PDF_MODERN_ACCENT: PDFColor = (0.19, 0.30, 0.33)


class MemoirBookOutputExportLayout(StrEnum):
    STANDARD = "standard"
    LARGE_PRINT = "large_print"


@dataclass(frozen=True)
class PDFLayout:
    page_width: int
    page_height: int
    font_size: int
    line_height: int
    margin_x: int
    margin_top: int
    margin_bottom: int
    title_font_size: int
    heading_font_size: int
    small_font_size: int
    max_line_units: int
    filename_suffix: str = ""
    reading_edition: bool = False
    mirror_body_pages: bool = False
    inner_margin_delta: int = 0
    manuscript_template: str = "classic_family"


PDF_LAYOUTS = {
    MemoirBookOutputExportLayout.STANDARD: PDFLayout(
        page_width=420,
        page_height=595,
        font_size=14,
        line_height=24,
        margin_x=44,
        margin_top=56,
        margin_bottom=56,
        title_font_size=24,
        heading_font_size=19,
        small_font_size=11,
        max_line_units=22,
        filename_suffix="阅读版",
        reading_edition=True,
    ),
    MemoirBookOutputExportLayout.LARGE_PRINT: PDFLayout(
        page_width=420,
        page_height=595,
        font_size=16,
        line_height=26,
        margin_x=48,
        margin_top=62,
        margin_bottom=50,
        title_font_size=26,
        heading_font_size=20,
        small_font_size=11,
        max_line_units=20,
        filename_suffix="打印版",
        mirror_body_pages=True,
        inner_margin_delta=10,
    ),
}


def configured_pdf_layout(
    base: PDFLayout,
    *,
    template: str,
    custom_layout: dict[str, str] | None = None,
) -> PDFLayout:
    """Apply a manuscript design while retaining the reading/print page behavior."""
    if template == "classic_family":
        return base
    if template == "photo_memory":
        font_delta, line_delta, margin_delta = 0, 5, 10
        title_delta, heading_delta, small_delta = 6, 3, 0
    elif template == "oral_archive":
        font_delta, line_delta, margin_delta = -1, -3, -6
        title_delta, heading_delta, small_delta = -2, -1, -1
    elif template == "heritage_classic":
        font_delta, line_delta, margin_delta = 0, 2, 6
        title_delta, heading_delta, small_delta = 3, 1, 0
    elif template == "warm_journal":
        font_delta, line_delta, margin_delta = 0, 3, 2
        title_delta, heading_delta, small_delta = 2, 1, 0
    elif template == "modern_minimal":
        font_delta, line_delta, margin_delta = 0, 2, 4
        title_delta, heading_delta, small_delta = 4, 1, 0
    elif template == "custom":
        choices = custom_layout or {}
        font_delta = 2 if choices.get("font_scale") == "large" else 0
        line_delta = {"compact": -3, "standard": 0, "relaxed": 5}.get(
            choices.get("line_spacing"), 0
        ) + font_delta
        margin_delta = {"narrow": -10, "standard": 0, "wide": 12}.get(
            choices.get("page_margin"), 0
        )
        title_delta, heading_delta, small_delta = font_delta + 2, font_delta, font_delta
    else:
        return base

    font_size = base.font_size + font_delta
    margin_x = base.margin_x + margin_delta
    max_line_units = round(
        base.max_line_units
        * (base.page_width - margin_x * 2)
        / (base.page_width - base.margin_x * 2)
        * base.font_size
        / font_size
    )
    return replace(
        base,
        font_size=font_size,
        line_height=max(font_size + 5, base.line_height + line_delta),
        margin_x=margin_x,
        margin_top=base.margin_top + margin_delta,
        margin_bottom=base.margin_bottom + margin_delta,
        title_font_size=base.title_font_size + title_delta,
        heading_font_size=base.heading_font_size + heading_delta,
        small_font_size=base.small_font_size + small_delta,
        max_line_units=max(12, max_line_units),
        manuscript_template=template,
    )


@dataclass
class PDFTextCommand:
    text: str
    x: int
    y: int
    font_size: int
    target_anchor: str | None = None
    color: PDFColor | None = None
    font_name: str | None = None
    char_spacing: float = 0.0


@dataclass
class PDFDecorationCommand:
    kind: str
    x: float
    y: float
    width: float
    height: float
    color: PDFColor
    stroke_color: PDFColor | None = None
    line_width: float = 0


@dataclass
class PDFImageCommand:
    path: Path
    x: float
    y: float
    width: float
    height: float


@dataclass
class PDFPage:
    commands: list[PDFTextCommand]
    decorations: list[PDFDecorationCommand] = field(default_factory=list)
    images: list[PDFImageCommand] = field(default_factory=list)
    background_color: PDFColor | None = None
    background_image_path: Path | None = None
    text_color: PDFColor | None = None
    header_title: str = ""
    section_title: str = ""
    show_footer: bool = True
    body_page_number: int | None = None
    anchor: str | None = None
    outline_title: str | None = None
    outline_level: int = 0


@dataclass(frozen=True)
class PDFTocEntry:
    index: int
    title: str
    anchor: str
    page_number: int | None = None
    section_titles: tuple[str, ...] = ()


LINE_START_FORBIDDEN_CHARS = set("，。！？；：、）】》」』”’〕］｝〉〗〙〛,.!?;:)]}")
LINE_END_FORBIDDEN_CHARS = set("（【《「『“‘([{")
COMMON_CHINESE_UNBREAKABLE_WORDS = {
    "那些",
    "事情",
    "因为",
    "所以",
    "但是",
    "然后",
    "还有",
    "这个",
    "那个",
    "我们",
    "你们",
    "他们",
    "自己",
    "时候",
    "如果",
    "不能",
    "可以",
    "没有",
    "已经",
    "现在",
    "后来",
    "以前",
    "一起",
    "家人",
    "工作",
    "朋友",
    "小时",
    "回忆",
}
CHINESE_NUMBER_UNITS = set("年月日岁个条章页册本段次分钟小时公里米斤元")


def _char_units(char: str) -> float:
    return 0.55 if char.isascii() else 1.0


def _fit_line_end(text: str, start: int, max_units: float) -> int:
    units = 0.0
    index = start
    while index < len(text):
        char_units = _char_units(text[index])
        if index > start and units + char_units > max_units:
            break
        units += char_units
        index += 1
        if units >= max_units:
            break
    return index


def _is_ascii_word_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in "-_./:%")


def _avoid_ascii_word_split(text: str, start: int, end: int) -> int:
    if end >= len(text) or end <= start + 1:
        return end
    if not (_is_ascii_word_char(text[end - 1]) and _is_ascii_word_char(text[end])):
        return end
    adjusted = end
    while adjusted > start + 1 and _is_ascii_word_char(text[adjusted - 1]):
        adjusted -= 1
    return adjusted if adjusted > start else end


def _avoid_common_chinese_word_split(text: str, start: int, end: int) -> int:
    if end >= len(text) or end <= start:
        return end
    if text[end - 1 : end + 1] in COMMON_CHINESE_UNBREAKABLE_WORDS:
        return end - 1 if end - 1 > start else end + 1
    return end


def _avoid_number_unit_split(text: str, start: int, end: int) -> int:
    if end >= len(text) or end <= start:
        return end
    if text[end] not in CHINESE_NUMBER_UNITS:
        return end
    left = end - 1
    while left >= start and text[left].isspace():
        left -= 1
    if left < start or not text[left].isdigit():
        return end
    number_start = left
    while number_start > start and (text[number_start - 1].isdigit() or text[number_start - 1] in ".,"):
        number_start -= 1
    while number_start > start and text[number_start - 1].isspace():
        number_start -= 1
    return number_start if number_start > start else end


def _adjust_forbidden_line_break(text: str, start: int, end: int) -> int:
    if end >= len(text):
        return end
    end = _avoid_ascii_word_split(text, start, end)
    end = _avoid_common_chinese_word_split(text, start, end)
    end = _avoid_number_unit_split(text, start, end)
    while end < len(text) and text[end] in LINE_START_FORBIDDEN_CHARS:
        end += 1
    while end > start + 1 and text[end - 1] in LINE_END_FORBIDDEN_CHARS:
        end -= 1
    return max(end, start + 1)


def _wrap_pdf_line(
    text: str,
    max_units: int,
    *,
    first_line_units: float | None = None,
) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    start = 0
    line_units = first_line_units or max_units
    while start < len(text):
        end = _fit_line_end(text, start, line_units)
        end = _adjust_forbidden_line_break(text, start, end)
        lines.append(text[start:end])
        start = end
        line_units = max_units
    return lines


def _text_units(text: str) -> float:
    return sum(_char_units(char) for char in text)


def _estimated_text_width(text: str, font_size: int, *, char_spacing: float = 0.0) -> int:
    spacing_width = max(0, len(text) - 1) * char_spacing
    return int(_text_units(text) * font_size + spacing_width)


def _right_aligned_x(
    text: str,
    layout: PDFLayout,
    font_size: int,
    *,
    char_spacing: float = 0.0,
    margin_x: int | None = None,
) -> int:
    margin = layout.margin_x if margin_x is None else margin_x
    return max(
        margin,
        layout.page_width - margin - _estimated_text_width(text, font_size, char_spacing=char_spacing),
    )


def _centered_x(text: str, layout: PDFLayout, font_size: int, *, char_spacing: float = 0.0) -> int:
    return max(
        layout.margin_x,
        int((layout.page_width - _estimated_text_width(text, font_size, char_spacing=char_spacing)) / 2),
    )


class PDFBookBuilder:
    def __init__(self, *, title: str, layout: PDFLayout):
        self.title = title
        self.layout = layout
        self.pages: list[PDFPage] = []
        self.current_page: PDFPage | None = None
        self.current_section = ""
        self.current_body_numbering = False
        self.continuation_show_header = True
        self.continuation_show_footer = True
        self.body_page_count = 0
        self.y = 0

    def _content_left(self) -> int:
        if not self.current_page or not self.layout.mirror_body_pages:
            return self.layout.margin_x
        if self.current_page.body_page_number is None:
            return self.layout.margin_x
        page_index = len(self.pages)
        offset = self.layout.inner_margin_delta
        return self.layout.margin_x + (offset if page_index % 2 else -offset)

    def new_page(
        self,
        section_title: str = "",
        *,
        show_header: bool = True,
        show_footer: bool = True,
        body_numbering: bool = False,
        continuation_show_header: bool | None = None,
        continuation_show_footer: bool | None = None,
        anchor: str | None = None,
        outline_title: str | None = None,
        outline_level: int = 0,
        background_color: PDFColor | None = None,
        background_image_path: Path | None = None,
        text_color: PDFColor | None = None,
    ) -> None:
        self.current_section = section_title or self.current_section
        self.current_body_numbering = body_numbering
        self.continuation_show_header = (
            show_header if continuation_show_header is None else continuation_show_header
        )
        self.continuation_show_footer = (
            show_footer if continuation_show_footer is None else continuation_show_footer
        )
        body_page_number = None
        if body_numbering:
            self.body_page_count += 1
            body_page_number = self.body_page_count
        self.current_page = PDFPage(
            commands=[],
            background_color=background_color,
            background_image_path=background_image_path,
            text_color=text_color,
            header_title=self.title if show_header else "",
            section_title=self.current_section,
            show_footer=show_footer,
            body_page_number=body_page_number,
            anchor=anchor,
            outline_title=outline_title,
            outline_level=outline_level,
        )
        self.pages.append(self.current_page)
        self.y = self.layout.page_height - self.layout.margin_top

    def add_decoration(
        self,
        *,
        kind: str,
        x: float,
        y: float,
        width: float,
        height: float,
        color: PDFColor,
        stroke_color: PDFColor | None = None,
        line_width: float = 0,
    ) -> None:
        page = self._ensure_page()
        page.decorations.append(
            PDFDecorationCommand(
                kind=kind,
                x=x,
                y=y,
                width=width,
                height=height,
                color=color,
                stroke_color=stroke_color,
                line_width=line_width,
            )
        )

    def _ensure_page(self) -> PDFPage:
        if not self.current_page:
            self.new_page(self.current_section or "正文")
        assert self.current_page is not None
        return self.current_page

    def _ensure_space(self, line_count: int = 1) -> None:
        required = max(1, line_count) * self.layout.line_height
        self._ensure_space_height(required)

    def _ensure_space_height(self, required: float) -> None:
        if not self.current_page or self.y - required < self.layout.margin_bottom:
            self.new_page(
                self.current_section or "正文",
                show_header=self.continuation_show_header,
                show_footer=self.continuation_show_footer,
                body_numbering=self.current_body_numbering,
                continuation_show_header=self.continuation_show_header,
                continuation_show_footer=self.continuation_show_footer,
            )

    def add_image(
        self,
        image_path: Path,
        *,
        display_size: str = "full_width",
        max_height: int | None = None,
    ) -> bool:
        if not image_path.exists() or not image_path.is_file():
            return False
        width_ratio = 1.0
        if display_size == "half_width":
            width_ratio = 0.68
        elif display_size == "inline":
            width_ratio = 0.82
        box_width = int((self.layout.page_width - self.layout.margin_x * 2) * width_ratio)
        box_height = max_height or min(220, int(self.layout.page_height * 0.34))
        self._ensure_space_height(box_height + 18)
        page = self._ensure_page()
        x = self._content_left() + int(
            (self.layout.page_width - self.layout.margin_x * 2 - box_width) / 2
        )
        y = self.y - box_height
        page.images.append(PDFImageCommand(path=image_path, x=x, y=y, width=box_width, height=box_height))
        self.y = y - 15
        return True

    def add_spacer(self, lines: float = 1.0) -> None:
        self._ensure_space(1)
        self.y -= int(self.layout.line_height * lines)

    def add_text(
        self,
        text: str,
        *,
        font_size: int | None = None,
        line_height: int | None = None,
        max_units: int | None = None,
        indent: int = 0,
        first_line_indent: int = 0,
        paragraph_spacing_after: int = 0,
        spacing_after: int = 0,
        min_start_lines: int = 1,
        keep_last_lines: int = 1,
        target_anchor: str | None = None,
        font_name: str | None = None,
        color: PDFColor | None = None,
    ) -> None:
        font_size = font_size or self.layout.font_size
        line_height = line_height or self.layout.line_height
        max_units = max_units or self.layout.max_line_units
        for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            stripped = raw_line.strip()
            if not stripped:
                self._ensure_space(1)
                self.y -= max(8, line_height // 2)
                continue
            first_line_units = max(6, max_units - first_line_indent / max(font_size, 1))
            lines = _wrap_pdf_line(
                stripped,
                max_units,
                first_line_units=first_line_units if first_line_indent else None,
            )
            if len(lines) > 1 and min_start_lines > 1:
                self._ensure_space(min(min_start_lines, len(lines)))
            for line_index, line in enumerate(lines):
                remaining_lines = len(lines) - line_index
                if keep_last_lines > 1 and remaining_lines == keep_last_lines:
                    self._ensure_space(remaining_lines)
                else:
                    self._ensure_space(1)
                page = self._ensure_page()
                line_indent = indent + (first_line_indent if line_index == 0 else 0)
                page.commands.append(
                    PDFTextCommand(
                        text=line,
                        x=self._content_left() + line_indent,
                        y=self.y,
                        font_size=font_size,
                        target_anchor=target_anchor,
                        font_name=font_name,
                        color=color,
                    )
                )
                self.y -= line_height
            if paragraph_spacing_after:
                self.y -= paragraph_spacing_after
        if spacing_after:
            self.y -= spacing_after

    def add_body_text(self, text: str, *, spacing_after: int = 0) -> None:
        self.add_text(
            text,
            first_line_indent=self.layout.font_size * 2,
            paragraph_spacing_after=max(3, self.layout.line_height // 5),
            spacing_after=spacing_after,
            min_start_lines=2,
            keep_last_lines=2,
            font_name=(
                PDF_SANS_FONT_NAME
                if self.layout.manuscript_template == "modern_minimal"
                else None
            ),
        )

    def add_quote(self, text: str, *, spacing_after: int = 0) -> None:
        template = self.layout.manuscript_template
        first_page_index = max(0, len(self.pages) - 1)
        first_page_command_count = len(self.current_page.commands) if self.current_page else 0
        self.add_text(
            f"“{text}”",
            font_size=max(self.layout.small_font_size + 1, self.layout.font_size - 2),
            line_height=self.layout.line_height,
            max_units=self.layout.max_line_units,
            indent=18 if template in {"heritage_classic", "warm_journal"} else 12,
            paragraph_spacing_after=0,
            spacing_after=spacing_after,
            min_start_lines=2,
            keep_last_lines=2,
            font_name=PDF_SANS_FONT_NAME if template == "modern_minimal" else None,
            color={
                "heritage_classic": PDF_HERITAGE_ACCENT,
                "warm_journal": PDF_WARM_ACCENT,
                "modern_minimal": PDF_MODERN_ACCENT,
            }.get(template),
        )
        if template not in {"heritage_classic", "warm_journal", "modern_minimal"}:
            return
        for page_index, page in enumerate(self.pages[first_page_index:], start=first_page_index):
            quote_lines = page.commands[
                first_page_command_count if page_index == first_page_index else 0 :
            ]
            if not quote_lines:
                continue
            top = max(line.y for line in quote_lines)
            bottom = min(line.y for line in quote_lines)
            left = min(line.x for line in quote_lines)
            if template == "modern_minimal":
                page.decorations.append(
                    PDFDecorationCommand(
                        kind="line",
                        x=left,
                        y=top + 16,
                        width=min(70, self.layout.page_width - left - self.layout.margin_x),
                        height=0,
                        color=PDF_MODERN_ACCENT,
                        line_width=1.1,
                    )
                )
            else:
                page.decorations.append(
                    PDFDecorationCommand(
                        kind="line",
                        x=left - 10,
                        y=bottom - 4,
                        width=0,
                        height=top - bottom + self.layout.font_size + 8,
                        color=(
                            PDF_HERITAGE_ACCENT
                            if template == "heritage_classic"
                            else PDF_WARM_ACCENT
                        ),
                        line_width=0.65 if template == "heritage_classic" else 2.2,
                    )
                )

    def add_heading(self, text: str) -> None:
        self._ensure_space(3)
        self.add_text(
            text,
            font_size=self.layout.heading_font_size,
            line_height=self.layout.line_height + 4,
            max_units=min(
                self.layout.max_line_units - 2,
                (self.layout.page_width - self.layout.margin_x * 2) // self.layout.heading_font_size,
            ),
            spacing_after=6,
            font_name=PDF_SERIF_BOLD_FONT_NAME,
        )

    def add_section_heading(self, text: str) -> None:
        template = self.layout.manuscript_template
        font_size = self.layout.font_size + 2
        indent = 11 if template in {"warm_journal", "modern_minimal"} else 0
        max_units = min(
            self.layout.max_line_units - 2,
            (
                self.layout.page_width
                - self.layout.margin_x * 2
                - self.layout.inner_margin_delta
                - indent
            ) // font_size,
        )
        self._ensure_space(
            max(3, len(_wrap_pdf_line(text.strip(), max_units)) + 1)
            if template in {"heritage_classic", "warm_journal", "modern_minimal"}
            else 3
        )
        heading_y = self.y
        heading_x = self._content_left()
        self.add_text(
            text,
            font_size=font_size,
            line_height=self.layout.line_height + 2,
            max_units=max_units,
            indent=indent,
            spacing_after=(
                16 if template in {"heritage_classic", "modern_minimal"}
                else 8 if template == "warm_journal" else 5
            ),
            font_name=(
                PDF_SANS_FONT_NAME
                if template == "modern_minimal"
                else PDF_SERIF_BOLD_FONT_NAME
            ),
            color={
                "heritage_classic": PDF_HERITAGE_ACCENT,
                "warm_journal": PDF_WARM_ACCENT,
                "modern_minimal": PDF_MODERN_ACCENT,
            }.get(template),
        )
        if template == "heritage_classic":
            self.add_decoration(
                kind="line", x=heading_x, y=self.y + 17, width=74, height=0,
                color=PDF_HERITAGE_ACCENT, line_width=0.5,
            )
        elif template in {"warm_journal", "modern_minimal"}:
            self.add_decoration(
                kind="rect",
                x=heading_x,
                y=heading_y - 3,
                width=3 if template == "warm_journal" else 4,
                height=font_size + 3,
                color=PDF_WARM_ACCENT if template == "warm_journal" else PDF_MODERN_ACCENT,
            )
            if template == "modern_minimal":
                self.add_decoration(
                    kind="line", x=heading_x, y=self.y + 17,
                    width=self.layout.page_width - 2 * self.layout.margin_x,
                    height=0, color=(0.83, 0.86, 0.86), line_width=0.5,
                )

    def add_centered(
        self,
        text: str,
        *,
        font_size: int,
        line_height: int,
        max_units: int | None = None,
        spacing_after: int = 0,
        char_spacing: float = 0.0,
        font_name: str | None = None,
    ) -> None:
        max_units = max_units or max(10, self.layout.max_line_units - 4)
        page = self._ensure_page()
        for line in _wrap_pdf_line(text.strip(), max_units):
            self._ensure_space(1)
            page = self._ensure_page()
            page.commands.append(
                PDFTextCommand(
                    text=line,
                    x=_centered_x(line, self.layout, font_size, char_spacing=char_spacing),
                    y=self.y,
                    font_size=font_size,
                    char_spacing=char_spacing,
                    font_name=font_name,
                )
            )
            self.y -= line_height
        if spacing_after:
            self.y -= spacing_after

    def add_centered_at(
        self,
        text: str,
        *,
        y: int,
        font_size: int,
        line_height: int,
        max_units: int | None = None,
        spacing_after: int = 0,
        color: PDFColor | None = None,
        font_name: str | None = None,
        char_spacing: float = 0.0,
        x_offset: int = 0,
    ) -> int:
        max_units = max_units or max(10, self.layout.max_line_units - 4)
        current_y = y
        for line in _wrap_pdf_line(text.strip(), max_units):
            page = self._ensure_page()
            page.commands.append(
                PDFTextCommand(
                    text=line,
                    x=_centered_x(line, self.layout, font_size, char_spacing=char_spacing) + x_offset,
                    y=current_y,
                    font_size=font_size,
                    color=color,
                    font_name=font_name,
                    char_spacing=char_spacing,
                )
            )
            current_y -= line_height
        return current_y - spacing_after

    def add_toc_row(
        self,
        *,
        label: str,
        page_text: str,
        target_anchor: str | None = None,
        indent: int = 0,
        font_size: int | None = None,
        font_name: str | None = None,
    ) -> None:
        font_size = font_size or self.layout.font_size
        line_height = self.layout.line_height + 2
        label_x = self.layout.margin_x + indent
        page_x = (
            self.layout.page_width
            - self.layout.margin_x
            - _estimated_text_width(page_text, font_size)
        )
        hanging_indent = min(font_size * 2, max(12, (page_x - label_x) // 4))
        continuation_x = label_x + hanging_indent
        max_label_units = max(
            8,
            (page_x - continuation_x - 8) / max(font_size, 1),
        )
        label_lines = _wrap_pdf_line(label.strip(), max_label_units)
        self._ensure_space(len(label_lines))
        for line_index, line in enumerate(label_lines):
            page = self._ensure_page()
            is_last_line = line_index == len(label_lines) - 1
            line_x = label_x if line_index == 0 else continuation_x
            page.commands.append(
                PDFTextCommand(
                    text=line,
                    x=line_x,
                    y=self.y,
                    font_size=font_size,
                    target_anchor=target_anchor,
                    font_name=font_name,
                )
            )
            if is_last_line and page_text:
                label_end_x = line_x + _estimated_text_width(line, font_size)
                leader_x = label_end_x + 6
                leader_units = max(0, (page_x - leader_x - 6) / max(font_size, 1))
                leaders = (
                    "." * max(2, int(leader_units / _char_units(".")))
                    if leader_units > 2
                    else ""
                )
                if leaders:
                    page.commands.append(
                        PDFTextCommand(
                            text=leaders,
                            x=leader_x,
                            y=self.y,
                            font_size=font_size,
                            target_anchor=target_anchor,
                        )
                    )
                page.commands.append(
                    PDFTextCommand(
                        text=page_text,
                        x=page_x,
                        y=self.y,
                        font_size=font_size,
                        target_anchor=target_anchor,
                    )
                )
            self.y -= line_height


def _pdf_page_commands(page: PDFPage, *, page_number: int, layout: PDFLayout) -> list[PDFTextCommand]:
    commands: list[PDFTextCommand] = []
    mirrored_body = layout.mirror_body_pages and page.body_page_number is not None
    outer_margin = max(0, layout.margin_x - layout.inner_margin_delta)
    accent_color = {
        "heritage_classic": PDF_HERITAGE_ACCENT,
        "warm_journal": PDF_WARM_ACCENT,
        "modern_minimal": PDF_MODERN_ACCENT,
    }.get(layout.manuscript_template, PDF_HEADER_TEXT)
    running_font = (
        PDF_SERIF_FONT_NAME
        if layout.manuscript_template in {"heritage_classic", "warm_journal"}
        else PDF_SANS_FONT_NAME
    )
    if page.header_title:
        header_y = layout.page_height - 28
        header_font_size = max(8, layout.small_font_size - 1)
        if mirrored_body and page_number % 2 == 1:
            if page.section_title:
                commands.append(
                    PDFTextCommand(
                        page.section_title[:14],
                        _right_aligned_x(
                            page.section_title[:14],
                            layout,
                            header_font_size,
                            margin_x=outer_margin,
                        ),
                        header_y,
                        header_font_size,
                        color=accent_color,
                        font_name=running_font,
                    )
                )
        elif mirrored_body:
            commands.append(
                PDFTextCommand(
                    page.header_title[:18],
                    outer_margin,
                    header_y,
                    header_font_size,
                    color=accent_color,
                    font_name=running_font,
                )
            )
        else:
            commands.append(
                PDFTextCommand(
                    page.header_title[:18],
                    layout.margin_x,
                    header_y,
                    header_font_size,
                    color=accent_color,
                    font_name=running_font,
                )
            )
            if page.section_title:
                commands.append(
                    PDFTextCommand(
                        page.section_title[:14],
                        _right_aligned_x(page.section_title[:14], layout, header_font_size),
                        header_y,
                        header_font_size,
                        color=accent_color,
                        font_name=running_font,
                    )
                )
    commands.extend(page.commands)
    if page.show_footer and page.body_page_number is not None:
        footer = {
            "heritage_classic": f"· {page.body_page_number} ·",
            "warm_journal": f"{page.body_page_number}",
            "modern_minimal": f"{page.body_page_number:02}",
        }.get(layout.manuscript_template, str(page.body_page_number))
        if layout.mirror_body_pages:
            footer_x = (
                outer_margin
                if page_number % 2 == 0
                else layout.page_width
                - outer_margin
                - _estimated_text_width(footer, layout.small_font_size)
            )
        else:
            footer_x = _centered_x(footer, layout, layout.small_font_size)
        commands.append(
            PDFTextCommand(
                footer,
                footer_x,
                24,
                layout.small_font_size,
                color=accent_color,
                font_name=running_font,
            )
        )
    return commands


def _draw_pdf_decoration(pdf: pdf_canvas.Canvas, decoration: PDFDecorationCommand) -> None:
    pdf.setFillColorRGB(*decoration.color)
    if decoration.stroke_color:
        pdf.setStrokeColorRGB(*decoration.stroke_color)
        pdf.setLineWidth(decoration.line_width or 1)
    else:
        pdf.setStrokeColorRGB(*decoration.color)
        pdf.setLineWidth(decoration.line_width or 0)
    if decoration.kind == "ellipse":
        pdf.ellipse(
            decoration.x,
            decoration.y,
            decoration.x + decoration.width,
            decoration.y + decoration.height,
            stroke=1 if decoration.stroke_color else 0,
            fill=1,
        )
    elif decoration.kind == "line":
        pdf.setStrokeColorRGB(*(decoration.stroke_color or decoration.color))
        pdf.setLineWidth(decoration.line_width or 1)
        pdf.line(decoration.x, decoration.y, decoration.x + decoration.width, decoration.y + decoration.height)
    elif decoration.kind == "stroke_rect":
        pdf.setStrokeColorRGB(*(decoration.stroke_color or decoration.color))
        pdf.setLineWidth(decoration.line_width or 1)
        pdf.rect(
            decoration.x,
            decoration.y,
            decoration.width,
            decoration.height,
            stroke=1,
            fill=0,
        )
    else:
        pdf.rect(
            decoration.x,
            decoration.y,
            decoration.width,
            decoration.height,
            stroke=1 if decoration.stroke_color else 0,
            fill=1,
        )


def _register_pdf_font() -> None:
    global _PDF_FONT_REGISTERED
    if _PDF_FONT_REGISTERED:
        return
    with _PDF_FONT_REGISTER_LOCK:
        if _PDF_FONT_REGISTERED:
            return
        if not PDF_SANS_FONT_PATH.exists():
            raise RuntimeError(f"PDF font file not found: {PDF_SANS_FONT_PATH}")
        if not PDF_SERIF_FONT_PATH.exists():
            raise RuntimeError(f"PDF font file not found: {PDF_SERIF_FONT_PATH}")
        if not PDF_SERIF_BOLD_FONT_PATH.exists():
            raise RuntimeError(f"PDF font file not found: {PDF_SERIF_BOLD_FONT_PATH}")
        pdfmetrics.registerFont(TTFont(PDF_SANS_FONT_NAME, str(PDF_SANS_FONT_PATH)))
        pdfmetrics.registerFont(TTFont(PDF_SERIF_FONT_NAME, str(PDF_SERIF_FONT_PATH)))
        pdfmetrics.registerFont(TTFont(PDF_SERIF_BOLD_FONT_NAME, str(PDF_SERIF_BOLD_FONT_PATH)))
        _PDF_FONT_REGISTERED = True


def _optimized_pdf_image_source(command: PDFImageCommand) -> str | ImageReader:
    max_width = max(1, round(command.width * PDF_EMBEDDED_IMAGE_DPI / 72))
    max_height = max(1, round(command.height * PDF_EMBEDDED_IMAGE_DPI / 72))
    with Image.open(command.path) as original:
        source_format = (original.format or "").upper()
        with ImageOps.exif_transpose(original) as source:
            within_print_size = source.width <= max_width and source.height <= max_height
            if within_print_size and source_format in {"JPEG", "JPG"}:
                return str(command.path)

            if not within_print_size:
                source.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            image = source.convert("RGBA")
            if image.getchannel("A").getextrema()[0] < 255:
                output = BytesIO()
                image.save(
                    output,
                    format="PNG",
                    optimize=True,
                    dpi=(PDF_EMBEDDED_IMAGE_DPI, PDF_EMBEDDED_IMAGE_DPI),
                )
                output.seek(0)
                return ImageReader(output)
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))

    output = BytesIO()
    background.save(
        output,
        format="JPEG",
        quality=PDF_EMBEDDED_IMAGE_JPEG_QUALITY,
        optimize=True,
        dpi=(PDF_EMBEDDED_IMAGE_DPI, PDF_EMBEDDED_IMAGE_DPI),
    )
    output.seek(0)
    return ImageReader(output)


def _render_pdf_pages(
    pages: list[PDFPage],
    layout: PDFLayout,
    *,
    metadata_title: str | None = None,
    metadata_author: str | None = None,
) -> bytes:
    if not pages:
        fallback = PDFBookBuilder(title="我的回忆录", layout=layout)
        fallback.new_page("正文")
        fallback.add_text("我的回忆录")
        pages = fallback.pages

    _register_pdf_font()
    output = BytesIO()
    pdf = pdf_canvas.Canvas(
        output,
        pagesize=(layout.page_width, layout.page_height),
        pageCompression=1,
        initialFontName=PDF_FONT_NAME,
        initialFontSize=layout.font_size,
        initialLeading=layout.line_height,
    )
    first_title = metadata_title or next((page.header_title for page in pages if page.header_title), "家庭回忆录")
    pdf.setTitle(first_title)
    pdf.setAuthor(metadata_author or "家人整理")
    pdf.setSubject("家庭回忆录")
    pdf.setKeywords("回忆录, 口述史, 家庭")
    pdf.setCreator(BRAND_NAME)
    if hasattr(pdf, "setProducer"):
        pdf.setProducer(BRAND_NAME)
    elif hasattr(pdf, "_doc") and hasattr(pdf._doc, "info"):
        pdf._doc.info.producer = BRAND_NAME
    image_sources: dict[tuple[Path, int, int], str | ImageReader] = {}
    for page_number, page in enumerate(pages, start=1):
        if page.background_color:
            pdf.setFillColorRGB(*page.background_color)
            pdf.rect(0, 0, layout.page_width, layout.page_height, stroke=0, fill=1)
        if page.background_image_path:
            pdf.drawImage(
                str(page.background_image_path),
                0,
                0,
                width=layout.page_width,
                height=layout.page_height,
                preserveAspectRatio=False,
                mask="auto",
            )
        for decoration in page.decorations:
            _draw_pdf_decoration(pdf, decoration)
        for image in page.images:
            image_key = (image.path, round(image.width), round(image.height))
            image_source = image_sources.get(image_key)
            if image_source is None:
                image_source = _optimized_pdf_image_source(image)
                image_sources[image_key] = image_source
            pdf.saveState()
            try:
                pdf.drawImage(
                    image_source,
                    image.x,
                    image.y,
                    width=image.width,
                    height=image.height,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )
            finally:
                pdf.restoreState()
        if page.anchor:
            pdf.bookmarkPage(page.anchor)
            if page.outline_title:
                pdf.addOutlineEntry(page.outline_title, page.anchor, level=page.outline_level)
        for command in _pdf_page_commands(page, page_number=page_number, layout=layout):
            if not command.text:
                continue
            font_name = command.font_name or PDF_FONT_NAME
            pdf.setFont(font_name, command.font_size)
            pdf.setFillColorRGB(*(command.color or page.text_color or PDF_BODY_TEXT))
            if command.char_spacing:
                text_object = pdf.beginText(command.x, command.y)
                text_object.setFont(font_name, command.font_size)
                text_object.setCharSpace(command.char_spacing)
                if hasattr(text_object, "setFillColorRGB"):
                    text_object.setFillColorRGB(*(command.color or page.text_color or PDF_BODY_TEXT))
                text_object.textOut(command.text)
                pdf.drawText(text_object)
            else:
                pdf.drawString(command.x, command.y, command.text)
            if command.target_anchor:
                width = max(24, _estimated_text_width(command.text, command.font_size, char_spacing=command.char_spacing))
                pdf.linkRect(
                    "",
                    command.target_anchor,
                    (command.x, command.y - 2, command.x + width, command.y + command.font_size + 2),
                    relative=0,
                    thickness=0,
                )
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def _truncate_units(text: str, max_units: float) -> str:
    if _text_units(text) <= max_units:
        return text
    current = ""
    current_units = 0.0
    for char in text:
        char_units = _char_units(char)
        if current and current_units + char_units > max_units:
            return f"{current}..."
        current += char
        current_units += char_units
    return current
