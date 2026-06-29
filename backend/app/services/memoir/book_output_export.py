from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.file_exports import ExportFile, safe_filename


PDF_FONT_NAME = "MemoirNotoSansSC"
PDF_FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "NotoSansSC-Regular.ttf"
)
_PDF_FONT_REGISTERED = False
PDFColor = tuple[float, float, float]
PDF_COVER_BACKGROUND: PDFColor = (0.96, 0.93, 0.87)
PDF_COVER_ACCENT_GREEN: PDFColor = (0.73, 0.82, 0.73)
PDF_COVER_ACCENT_TEA: PDFColor = (0.72, 0.51, 0.43)
PDF_COVER_LINE: PDFColor = (0.58, 0.32, 0.27)


class MemoirBookOutputExportFormat(StrEnum):
    TXT = "txt"
    PDF = "pdf"


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


PDF_LAYOUTS = {
    MemoirBookOutputExportLayout.STANDARD: PDFLayout(
        page_width=420,
        page_height=595,
        font_size=12,
        line_height=20,
        margin_x=42,
        margin_top=56,
        margin_bottom=56,
        title_font_size=24,
        heading_font_size=18,
        small_font_size=10,
        max_line_units=27,
        filename_suffix="阅读版",
    ),
    MemoirBookOutputExportLayout.LARGE_PRINT: PDFLayout(
        page_width=420,
        page_height=595,
        font_size=16,
        line_height=25,
        margin_x=46,
        margin_top=62,
        margin_bottom=50,
        title_font_size=26,
        heading_font_size=20,
        small_font_size=11,
        max_line_units=20,
        filename_suffix="打印版",
    ),
}


@dataclass
class PDFTextCommand:
    text: str
    x: int
    y: int
    font_size: int
    target_anchor: str | None = None


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
class PDFPage:
    commands: list[PDFTextCommand]
    decorations: list[PDFDecorationCommand] = field(default_factory=list)
    background_color: PDFColor | None = None
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


LINE_START_FORBIDDEN_CHARS = set("，。！？；：、）】》」』”’〕］｝〉〗〙〛,.!?;:)]}")
LINE_END_FORBIDDEN_CHARS = set("（【《「『“‘([{")


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


def _adjust_forbidden_line_break(text: str, start: int, end: int) -> int:
    if end >= len(text):
        return end
    end = _avoid_ascii_word_split(text, start, end)
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


def _estimated_text_width(text: str, font_size: int) -> int:
    return int(_text_units(text) * font_size)


def _right_aligned_x(text: str, layout: PDFLayout, font_size: int) -> int:
    return max(
        layout.margin_x,
        layout.page_width - layout.margin_x - _estimated_text_width(text, font_size),
    )


def _centered_x(text: str, layout: PDFLayout, font_size: int) -> int:
    return max(layout.margin_x, int((layout.page_width - _estimated_text_width(text, font_size)) / 2))


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
        if not self.current_page or self.y - required < self.layout.margin_bottom:
            self.new_page(
                self.current_section or "正文",
                show_header=self.continuation_show_header,
                show_footer=self.continuation_show_footer,
                body_numbering=self.current_body_numbering,
                continuation_show_header=self.continuation_show_header,
                continuation_show_footer=self.continuation_show_footer,
            )

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
                        x=self.layout.margin_x + line_indent,
                        y=self.y,
                        font_size=font_size,
                        target_anchor=target_anchor,
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
        )

    def add_quote(self, text: str, *, spacing_after: int = 0) -> None:
        self.add_text(
            f"“{text}”",
            font_size=self.layout.small_font_size + 1,
            line_height=self.layout.line_height,
            max_units=max(12, self.layout.max_line_units - 4),
            indent=12,
            paragraph_spacing_after=0,
            spacing_after=spacing_after,
            min_start_lines=2,
            keep_last_lines=2,
        )

    def add_heading(self, text: str) -> None:
        self._ensure_space(3)
        self.add_text(
            text,
            font_size=self.layout.heading_font_size,
            line_height=self.layout.line_height + 4,
            max_units=max(12, self.layout.max_line_units - 2),
            spacing_after=6,
        )

    def add_centered(
        self,
        text: str,
        *,
        font_size: int,
        line_height: int,
        max_units: int | None = None,
        spacing_after: int = 0,
    ) -> None:
        max_units = max_units or max(10, self.layout.max_line_units - 4)
        page = self._ensure_page()
        for line in _wrap_pdf_line(text.strip(), max_units):
            self._ensure_space(1)
            page = self._ensure_page()
            page.commands.append(
                PDFTextCommand(
                    text=line,
                    x=_centered_x(line, self.layout, font_size),
                    y=self.y,
                    font_size=font_size,
                )
            )
            self.y -= line_height
        if spacing_after:
            self.y -= spacing_after

    def add_toc_row(
        self,
        *,
        label: str,
        page_text: str,
        target_anchor: str | None = None,
    ) -> None:
        font_size = self.layout.font_size
        line_height = self.layout.line_height + 2
        self._ensure_space(1)
        page = self._ensure_page()
        page_x = (
            self.layout.page_width
            - self.layout.margin_x
            - _estimated_text_width(page_text, font_size)
        )
        max_label_units = max(8, (page_x - self.layout.margin_x - 22) / max(font_size, 1))
        fitted_label = _truncate_units(label, max_label_units)
        page.commands.append(
            PDFTextCommand(
                text=fitted_label,
                x=self.layout.margin_x,
                y=self.y,
                font_size=font_size,
                target_anchor=target_anchor,
            )
        )
        if page_text:
            label_end_x = self.layout.margin_x + _estimated_text_width(fitted_label, font_size)
            leader_x = label_end_x + 6
            leader_units = max(0, (page_x - leader_x - 6) / max(font_size, 1))
            leaders = "." * max(2, int(leader_units / _char_units("."))) if leader_units > 2 else ""
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
    if page.header_title:
        header_y = layout.page_height - 28
        commands.append(
            PDFTextCommand(page.header_title[:18], layout.margin_x, header_y, layout.small_font_size)
        )
        if page.section_title:
            commands.append(
                PDFTextCommand(
                    page.section_title[:14],
                    _right_aligned_x(page.section_title[:14], layout, layout.small_font_size),
                    header_y,
                    layout.small_font_size,
                )
            )
    commands.extend(page.commands)
    if page.show_footer and page.body_page_number is not None:
        footer = str(page.body_page_number)
        commands.append(
            PDFTextCommand(
                footer,
                _centered_x(footer, layout, layout.small_font_size),
                24,
                layout.small_font_size,
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
    if not PDF_FONT_PATH.exists():
        raise RuntimeError(f"PDF font file not found: {PDF_FONT_PATH}")
    pdfmetrics.registerFont(TTFont(PDF_FONT_NAME, str(PDF_FONT_PATH)))
    _PDF_FONT_REGISTERED = True


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
    )
    first_title = metadata_title or next((page.header_title for page in pages if page.header_title), "家庭回忆录")
    pdf.setTitle(first_title)
    pdf.setAuthor(metadata_author or "家人整理")
    pdf.setSubject("家庭回忆录")
    pdf.setKeywords("回忆录, 口述史, 家庭")
    pdf.setCreator("Pawtale")
    if hasattr(pdf, "setProducer"):
        pdf.setProducer("Pawtale")
    elif hasattr(pdf, "_doc") and hasattr(pdf._doc, "info"):
        pdf._doc.info.producer = "Pawtale"
    for page_number, page in enumerate(pages, start=1):
        if page.background_color:
            pdf.setFillColorRGB(*page.background_color)
            pdf.rect(0, 0, layout.page_width, layout.page_height, stroke=0, fill=1)
        for decoration in page.decorations:
            _draw_pdf_decoration(pdf, decoration)
        if page.anchor:
            pdf.bookmarkPage(page.anchor)
            if page.outline_title:
                pdf.addOutlineEntry(page.outline_title, page.anchor, level=page.outline_level)
        for command in _pdf_page_commands(page, page_number=page_number, layout=layout):
            if not command.text:
                continue
            pdf.setFont(PDF_FONT_NAME, command.font_size)
            pdf.drawString(command.x, command.y, command.text)
            if command.target_anchor:
                width = max(24, _estimated_text_width(command.text, command.font_size))
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


def _render_plain_pdf(content: str, title: str, layout: PDFLayout) -> bytes:
    builder = PDFBookBuilder(title=title, layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.add_heading(title)
    builder.add_body_text(content.strip() or title)
    return _render_pdf_pages(builder.pages, layout, metadata_title=title, metadata_author="家人整理")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _section_title(section: dict[str, Any], fallback: str) -> str:
    return _str(section.get("title")) or fallback


def _section_content(section: dict[str, Any]) -> str:
    return _str(section.get("content"))


def _section_enabled(section: dict[str, Any]) -> bool:
    return section.get("enabled") is not False


def _int(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) and value > 0 else fallback


def _today_date_text() -> str:
    return datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()


CHINESE_NUMERALS = "零一二三四五六七八九"


def _chinese_number(value: int) -> str:
    if value <= 0:
        return str(value)
    if value < 10:
        return CHINESE_NUMERALS[value]
    if value == 10:
        return "十"
    if value < 20:
        return f"十{CHINESE_NUMERALS[value % 10]}"
    if value < 100:
        tens, ones = divmod(value, 10)
        return f"{CHINESE_NUMERALS[tens]}十{CHINESE_NUMERALS[ones] if ones else ''}"
    return str(value)


def _add_section_page(
    builder: PDFBookBuilder,
    *,
    section_title: str,
    content: str,
    heading: str | None = None,
    show_header: bool = False,
    show_footer: bool = False,
    body_numbering: bool = False,
) -> None:
    if not content and not (heading or section_title):
        return
    builder.new_page(
        section_title,
        show_header=show_header,
        show_footer=show_footer,
        body_numbering=body_numbering,
    )
    builder.add_heading(heading or section_title)
    if content:
        builder.add_body_text(content)


def _add_cover_page(builder: PDFBookBuilder, *, title: str, subtitle: str, prepared_by: str) -> None:
    builder.new_page(show_header=False, show_footer=False, background_color=PDF_COVER_BACKGROUND)
    builder.add_decoration(
        kind="ellipse",
        x=builder.layout.page_width - 145,
        y=36,
        width=190,
        height=160,
        color=PDF_COVER_ACCENT_GREEN,
    )
    builder.add_decoration(
        kind="rect",
        x=0,
        y=builder.layout.page_height - 132,
        width=builder.layout.page_width,
        height=16,
        color=PDF_COVER_ACCENT_TEA,
    )
    builder.add_decoration(
        kind="line",
        x=builder.layout.margin_x,
        y=builder.layout.margin_bottom + 94,
        width=92,
        height=0,
        color=PDF_COVER_LINE,
        line_width=1.2,
    )
    builder.add_spacer(3.5)
    builder.add_centered(
        "家庭回忆录",
        font_size=builder.layout.small_font_size,
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        spacing_after=18,
    )
    builder.add_centered(
        title,
        font_size=builder.layout.title_font_size,
        line_height=builder.layout.line_height + 12,
        max_units=max(8, builder.layout.max_line_units - 6),
        spacing_after=10,
    )
    if subtitle:
        builder.add_centered(
            subtitle,
            font_size=builder.layout.font_size,
            line_height=builder.layout.line_height + 4,
            max_units=builder.layout.max_line_units - 2,
            spacing_after=16,
        )
    if prepared_by:
        builder.y = max(builder.layout.margin_bottom + 70, builder.y - 110)
        builder.add_centered(
            prepared_by,
            font_size=builder.layout.small_font_size,
            line_height=builder.layout.line_height,
            max_units=builder.layout.max_line_units,
        )
    builder.add_centered(
        _today_date_text()[:4],
        font_size=builder.layout.small_font_size,
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
    )


def _add_title_page(builder: PDFBookBuilder, *, title: str, content: str) -> None:
    builder.new_page("扉页", show_header=False, show_footer=False)
    builder.add_spacer(4.5)
    builder.add_centered(
        title,
        font_size=builder.layout.title_font_size,
        line_height=builder.layout.line_height + 12,
        max_units=max(8, builder.layout.max_line_units - 6),
        spacing_after=24,
    )
    for raw_line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line == title:
            builder.add_spacer(0.5)
            continue
        builder.add_centered(
            line,
            font_size=builder.layout.small_font_size + 1,
            line_height=builder.layout.line_height,
            max_units=builder.layout.max_line_units - 4,
            spacing_after=4,
        )


def _title_page_content(subtitle: str, content: str) -> str:
    first_content_line = next(
        (line.strip() for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()),
        "",
    )
    if not subtitle or first_content_line == subtitle:
        return content
    return "\n".join(part for part in [subtitle, content] if part)


def _add_front_matter(builder: PDFBookBuilder, front_matter: list[Any], *, title: str, subtitle: str) -> None:
    for raw_section in front_matter:
        section = _dict(raw_section)
        if not _section_enabled(section):
            continue
        kind = _str(section.get("kind"))
        if kind == "cover":
            continue
        section_title = _section_title(section, "书前说明")
        content = _section_content(section)
        if kind == "title_page":
            _add_title_page(builder, title=title, content=_title_page_content(subtitle, content))
            continue
        _add_section_page(builder, section_title=section_title, content=content)


def _chapter_title(chapter: dict[str, Any], fallback_index: int) -> str:
    index = _int(chapter.get("index"), fallback_index)
    title = _str(chapter.get("title")) or f"第{index}章"
    return title


def _chapter_label(chapter: dict[str, Any], fallback_index: int) -> str:
    index = _int(chapter.get("index"), fallback_index)
    return f"第{_chinese_number(index)}章"


def _chapter_anchor(fallback_index: int) -> str:
    return f"chapter-{fallback_index}"


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


def _toc_line(entry: PDFTocEntry, layout: PDFLayout) -> str:
    page_text = str(entry.page_number) if entry.page_number is not None else ""
    label = f"第{_chinese_number(entry.index)}章　{entry.title}"
    if not page_text:
        return label
    label = _truncate_units(label, max(8, layout.max_line_units - _text_units(page_text) - 4))
    leader_units = layout.max_line_units - _text_units(label) - _text_units(page_text) - 2
    leaders = "." * max(2, int(leader_units / _char_units(".")))
    return f"{label} {leaders} {page_text}"


def _chapter_toc_entries(
    *,
    toc_items: list[Any],
    chapters: list[Any],
    page_numbers: dict[str, int] | None = None,
) -> list[PDFTocEntry]:
    page_numbers = page_numbers or {}
    items = toc_items or chapters
    entries: list[PDFTocEntry] = []
    for fallback_index, raw_item in enumerate(items, start=1):
        item = _dict(raw_item)
        index = _int(item.get("index"), fallback_index)
        title = _str(item.get("title")) or f"第{index}章"
        anchor = _chapter_anchor(fallback_index)
        entries.append(
            PDFTocEntry(
                index=index,
                title=title,
                anchor=anchor,
                page_number=page_numbers.get(anchor),
            )
        )
    return entries


def _add_table_of_contents(
    builder: PDFBookBuilder,
    *,
    entries: list[PDFTocEntry],
) -> None:
    builder.new_page("目录", show_header=False, show_footer=False)
    builder.add_heading("目录")
    if not entries:
        builder.add_text("暂无章节。")
        return
    for entry in entries:
        builder.add_toc_row(
            label=_toc_line(entry, builder.layout) if entry.page_number is None else f"第{_chinese_number(entry.index)}章　{entry.title}",
            page_text=str(entry.page_number) if entry.page_number is not None else "",
            target_anchor=entry.anchor if entry.page_number is not None else None,
        )


def _add_chapters(builder: PDFBookBuilder, chapters: list[Any]) -> None:
    for fallback_index, raw_chapter in enumerate(chapters, start=1):
        chapter = _dict(raw_chapter)
        title = _chapter_title(chapter, fallback_index)
        label = _chapter_label(chapter, fallback_index)
        anchor = _chapter_anchor(fallback_index)
        builder.new_page(
            title,
            show_header=False,
            show_footer=False,
            body_numbering=True,
            continuation_show_header=True,
            continuation_show_footer=True,
            anchor=anchor,
            outline_title=f"{label} {title}",
        )
        builder.add_spacer(2.5)
        builder.add_text(
            label,
            font_size=builder.layout.small_font_size + 1,
            line_height=builder.layout.line_height,
            max_units=builder.layout.max_line_units,
            spacing_after=6,
        )
        builder.add_text(
            title,
            font_size=builder.layout.title_font_size,
            line_height=builder.layout.line_height + 12,
            max_units=max(10, builder.layout.max_line_units - 4),
            spacing_after=12,
        )

        hints = [
            _str(chapter.get("time_period")),
            _str(chapter.get("place_hint")),
        ]
        hint_text = " · ".join(hint for hint in hints if hint)
        if hint_text:
            builder.add_text(
                hint_text,
                font_size=builder.layout.small_font_size,
                line_height=builder.layout.line_height,
            )
            builder.add_spacer(0.5)

        intro = _str(chapter.get("intro"))
        if intro:
            builder.add_body_text(intro, spacing_after=4)

        for raw_quote in _list(chapter.get("pull_quotes"))[:2]:
            quote = _str(_dict(raw_quote).get("text"))
            if quote:
                builder.add_quote(quote, spacing_after=6)
                break

        builder.add_spacer(0.5)

        content = _str(chapter.get("content"))
        if content:
            builder.add_body_text(content)


def _add_unconfirmed_facts(builder: PDFBookBuilder, facts: list[Any]) -> None:
    if not facts:
        return
    builder.new_page("待核实信息", show_header=True, show_footer=True, body_numbering=True)
    builder.add_heading("待核实信息")
    for index, raw_fact in enumerate(facts, start=1):
        fact = _dict(raw_fact)
        text = _str(fact.get("text"))
        note = _str(fact.get("note"))
        if not text and not note:
            continue
        builder.add_text(f"{index}. {text}" if text else f"{index}. {note}")
        if note and text:
            builder.add_text(
                note,
                font_size=builder.layout.small_font_size,
                line_height=builder.layout.line_height,
                indent=8,
            )


def _add_appendix_record_page(
    builder: PDFBookBuilder,
    sections: list[tuple[str, str]],
) -> None:
    if not sections:
        return
    builder.new_page("附录", show_header=True, show_footer=True, body_numbering=True)
    builder.add_heading("附录：整理记录")
    for title, content in sections:
        if not content:
            continue
        builder.add_text(
            title,
            font_size=builder.layout.small_font_size + 1,
            line_height=builder.layout.line_height,
            spacing_after=2,
        )
        builder.add_body_text(content, spacing_after=4)


def _add_back_matter(builder: PDFBookBuilder, back_matter: dict[str, Any], *, include_source_appendix: bool) -> None:
    afterword = _dict(back_matter.get("afterword"))
    if _section_enabled(afterword):
        content = _section_content(afterword)
        if content:
            _add_section_page(
                builder,
                section_title=_section_title(afterword, "后记"),
                content=content,
                show_header=True,
                show_footer=True,
                body_numbering=True,
            )
    _add_unconfirmed_facts(builder, _list(back_matter.get("unconfirmed_facts")))
    appendix_sections: list[tuple[str, str]] = []
    for key, fallback_title in [("interview_records", "讲述资料"), ("version_note", "版本与修订")]:
        section = _dict(back_matter.get(key))
        if _section_enabled(section):
            content = _section_content(section)
            if content:
                appendix_sections.append((_section_title(section, fallback_title), content))
    if include_source_appendix:
        section = _dict(back_matter.get("source_appendix"))
        if _section_enabled(section):
            content = _section_content(section)
            if content:
                appendix_sections.append((_section_title(section, "章节来源"), content))
    _add_appendix_record_page(builder, appendix_sections)


def _add_back_cover(builder: PDFBookBuilder, *, quote: str) -> None:
    builder.new_page(show_header=False, show_footer=False, background_color=PDF_COVER_BACKGROUND)
    builder.add_decoration(
        kind="ellipse",
        x=-54,
        y=builder.layout.page_height - 155,
        width=150,
        height=125,
        color=PDF_COVER_ACCENT_GREEN,
    )
    builder.add_decoration(
        kind="line",
        x=builder.layout.margin_x,
        y=builder.layout.page_height - 210,
        width=82,
        height=0,
        color=PDF_COVER_LINE,
        line_width=1,
    )
    builder.add_spacer(6)
    if quote:
        builder.add_centered(
            f"“{quote}”",
            font_size=builder.layout.font_size + 1,
            line_height=builder.layout.line_height + 4,
            max_units=max(10, builder.layout.max_line_units - 4),
            spacing_after=28,
        )
    builder.y = max(builder.layout.margin_bottom + 90, builder.y - 160)
    builder.add_centered(
        "本书根据口述访谈整理",
        font_size=builder.layout.small_font_size,
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        spacing_after=4,
    )
    builder.add_centered(
        f"生成于 {_today_date_text()}",
        font_size=builder.layout.small_font_size,
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
    )


def _chapter_page_numbers(pages: list[PDFPage]) -> dict[str, int]:
    page_numbers: dict[str, int] = {}
    for page in pages:
        if page.anchor and page.body_page_number is not None:
            page_numbers[page.anchor] = page.body_page_number
    return page_numbers


def _build_structured_manuscript_pdf_pages(
    *,
    book_title: str,
    subtitle: str,
    prepared_by: str,
    back_cover_quote: str,
    front_matter: list[Any],
    chapters: list[Any],
    back_matter: dict[str, Any],
    include_source_appendix: bool,
    toc_entries: list[PDFTocEntry],
    layout: PDFLayout,
) -> list[PDFPage]:
    builder = PDFBookBuilder(title=book_title, layout=layout)
    _add_cover_page(builder, title=book_title, subtitle=subtitle, prepared_by=prepared_by)
    _add_front_matter(builder, front_matter, title=book_title, subtitle=subtitle)
    _add_table_of_contents(builder, entries=toc_entries)
    _add_chapters(builder, chapters)
    _add_back_matter(builder, back_matter, include_source_appendix=include_source_appendix)
    _add_back_cover(builder, quote=back_cover_quote)
    return builder.pages


def _build_manuscript_pdf_pages(
    *,
    title: str,
    content: str,
    manuscript: dict[str, Any],
    layout: PDFLayout,
    show_source_appendix: bool | None = None,
) -> list[PDFPage]:
    profile = _dict(manuscript.get("profile"))
    chapters = _list(manuscript.get("chapters"))
    front_matter = _list(manuscript.get("front_matter"))
    back_matter = _dict(manuscript.get("back_matter"))
    if not profile and not chapters and not front_matter and not back_matter:
        builder = PDFBookBuilder(title=title, layout=layout)
        builder.new_page("正文", body_numbering=True)
        builder.add_heading(title)
        builder.add_body_text(content.strip() or title)
        return builder.pages

    book_title = _str(profile.get("title")) or title
    subtitle = _str(profile.get("subtitle"))
    prepared_by = _str(profile.get("prepared_by"))
    settings = _dict(manuscript.get("render_settings"))
    setting_source_appendix = settings.get("show_source_appendix")
    if setting_source_appendix is None:
        setting_source_appendix = settings.get("include_source_appendix")
    include_source_appendix = (
        show_source_appendix
        if show_source_appendix is not None
        else setting_source_appendix is not False
    )
    back_cover_quote = _str(settings.get("back_cover_quote"))

    toc_items = _list(manuscript.get("table_of_contents"))
    initial_entries = _chapter_toc_entries(toc_items=toc_items, chapters=chapters)
    initial_pages = _build_structured_manuscript_pdf_pages(
        book_title=book_title,
        subtitle=subtitle,
        prepared_by=prepared_by,
        back_cover_quote=back_cover_quote,
        front_matter=front_matter,
        chapters=chapters,
        back_matter=back_matter,
        include_source_appendix=include_source_appendix,
        toc_entries=initial_entries,
        layout=layout,
    )
    toc_entries = _chapter_toc_entries(
        toc_items=toc_items,
        chapters=chapters,
        page_numbers=_chapter_page_numbers(initial_pages),
    )
    return _build_structured_manuscript_pdf_pages(
        book_title=book_title,
        subtitle=subtitle,
        prepared_by=prepared_by,
        back_cover_quote=back_cover_quote,
        front_matter=front_matter,
        chapters=chapters,
        back_matter=back_matter,
        include_source_appendix=include_source_appendix,
        toc_entries=toc_entries,
        layout=layout,
    )


def _render_manuscript_pdf(
    *,
    title: str,
    content: str,
    manuscript: dict[str, Any],
    layout: PDFLayout,
    show_source_appendix: bool | None = None,
) -> bytes:
    profile = _dict((manuscript or {}).get("profile")) if isinstance(manuscript, dict) else {}
    metadata_title = _str(profile.get("title")) or title
    metadata_author = _str(profile.get("narrator_name")) or _str(profile.get("prepared_by")) or "家人整理"
    pages = _build_manuscript_pdf_pages(
        title=title,
        content=content,
        manuscript=manuscript,
        layout=layout,
        show_source_appendix=show_source_appendix,
    )
    return _render_pdf_pages(
        pages,
        layout,
        metadata_title=metadata_title,
        metadata_author=metadata_author,
    )


def _formal_export_title(raw_title: str) -> str:
    title = raw_title.strip().strip("《》「」“”\"' ").replace("章节规划", "").strip(" -_·：:")
    title = title.replace("主题回忆录", "回忆录").replace("主题", "").strip(" -_·：:")
    if not title or title == "回忆录":
        return "家庭回忆录"
    if title.endswith("的回忆录"):
        return title
    if title.endswith("回忆录"):
        subject = title[: -len("回忆录")].strip(" -_·：:的")
        return f"{subject}的回忆录" if subject else "家庭回忆录"
    return title


def _export_title(title: str, manuscript: dict[str, Any] | None) -> str:
    profile = _dict((manuscript or {}).get("profile"))
    return _formal_export_title(_str(profile.get("title")) or title)


def _filename_book_title(title: str) -> str:
    return "家庭回忆录" if title == "家庭回忆录" else f"《{title}》"


def _book_export_filename(
    title: str,
    version: int,
    extension: str,
    layout: MemoirBookOutputExportLayout,
) -> str:
    date_text = _today_date_text()
    suffix = PDF_LAYOUTS[layout].filename_suffix if extension == "pdf" else ""
    version_suffix = f"v{version}" if version > 1 else ""
    if extension == "pdf":
        parts = [_filename_book_title(title), suffix, date_text, version_suffix]
    else:
        parts = [_filename_book_title(title), date_text, version_suffix]
    return safe_filename("_".join(part for part in parts if part), extension)


def render_book_output_export(
    *,
    title: str,
    version: int,
    content: str,
    manuscript: dict[str, Any] | None = None,
    show_source_appendix: bool | None = None,
    export_format: MemoirBookOutputExportFormat,
    layout: MemoirBookOutputExportLayout = MemoirBookOutputExportLayout.STANDARD,
) -> ExportFile:
    export_title = _export_title(title, manuscript)
    if export_format == MemoirBookOutputExportFormat.PDF:
        pdf_layout = PDF_LAYOUTS.get(layout, PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD])
        return ExportFile(
            filename=_book_export_filename(export_title, version, "pdf", layout),
            media_type="application/pdf",
            content=_render_manuscript_pdf(
                title=export_title,
                content=content,
                manuscript=manuscript or {},
                layout=pdf_layout,
                show_source_appendix=show_source_appendix,
            ),
        )
    return ExportFile(
        filename=_book_export_filename(export_title, version, "txt", MemoirBookOutputExportLayout.STANDARD),
        media_type="text/plain; charset=utf-8",
        content=content + "\n",
    )
