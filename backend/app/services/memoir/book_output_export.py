from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class PDFPage:
    commands: list[PDFTextCommand]
    header_title: str = ""
    section_title: str = ""
    show_footer: bool = True
    body_page_number: int | None = None


def _wrap_pdf_line(text: str, max_units: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    current_units = 0.0
    for char in text:
        char_units = 0.55 if char.isascii() else 1.0
        if current and current_units + char_units > max_units:
            lines.append(current)
            current = char
            current_units = char_units
            continue
        current += char
        current_units += char_units
    if current:
        lines.append(current)
    return lines


def _text_units(text: str) -> float:
    return sum(0.55 if char.isascii() else 1.0 for char in text)


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
            header_title=self.title if show_header else "",
            section_title=self.current_section,
            show_footer=show_footer,
            body_page_number=body_page_number,
        )
        self.pages.append(self.current_page)
        self.y = self.layout.page_height - self.layout.margin_top

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
        spacing_after: int = 0,
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
            for line in _wrap_pdf_line(stripped, max_units):
                self._ensure_space(1)
                page = self._ensure_page()
                page.commands.append(
                    PDFTextCommand(
                        text=line,
                        x=self.layout.margin_x + indent,
                        y=self.y,
                        font_size=font_size,
                    )
                )
                self.y -= line_height
        if spacing_after:
            self.y -= spacing_after

    def add_heading(self, text: str) -> None:
        self._ensure_space(2)
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


def _register_pdf_font() -> None:
    global _PDF_FONT_REGISTERED
    if _PDF_FONT_REGISTERED:
        return
    if not PDF_FONT_PATH.exists():
        raise RuntimeError(f"PDF font file not found: {PDF_FONT_PATH}")
    pdfmetrics.registerFont(TTFont(PDF_FONT_NAME, str(PDF_FONT_PATH)))
    _PDF_FONT_REGISTERED = True


def _render_pdf_pages(pages: list[PDFPage], layout: PDFLayout) -> bytes:
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
    first_title = next((page.header_title for page in pages if page.header_title), "我的回忆录")
    pdf.setTitle(first_title)
    pdf.setAuthor("Pawtale")
    for page_number, page in enumerate(pages, start=1):
        for command in _pdf_page_commands(page, page_number=page_number, layout=layout):
            if not command.text:
                continue
            pdf.setFont(PDF_FONT_NAME, command.font_size)
            pdf.drawString(command.x, command.y, command.text)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def _render_plain_pdf(content: str, title: str, layout: PDFLayout) -> bytes:
    builder = PDFBookBuilder(title=title, layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.add_heading(title)
    builder.add_text(content.strip() or title)
    return _render_pdf_pages(builder.pages, layout)


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


def _int(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) and value > 0 else fallback


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
        builder.add_text(content)


def _add_cover_page(builder: PDFBookBuilder, *, title: str, subtitle: str, prepared_by: str) -> None:
    builder.new_page(show_header=False, show_footer=False)
    builder.add_spacer(5)
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


def _add_front_matter(builder: PDFBookBuilder, front_matter: list[Any], *, title: str, subtitle: str) -> None:
    for raw_section in front_matter:
        section = _dict(raw_section)
        kind = _str(section.get("kind"))
        if kind == "cover":
            continue
        section_title = _section_title(section, "书前说明")
        content = _section_content(section)
        if kind == "title_page":
            content = "\n".join(part for part in [subtitle, content] if part)
            _add_section_page(builder, section_title=section_title, heading=title, content=content)
            continue
        _add_section_page(builder, section_title=section_title, content=content)


def _chapter_title(chapter: dict[str, Any], fallback_index: int) -> str:
    index = _int(chapter.get("index"), fallback_index)
    title = _str(chapter.get("title")) or f"第{index}章"
    return title


def _chapter_label(chapter: dict[str, Any], fallback_index: int) -> str:
    index = _int(chapter.get("index"), fallback_index)
    return f"第{_chinese_number(index)}章"


def _add_table_of_contents(
    builder: PDFBookBuilder,
    *,
    toc_items: list[Any],
    chapters: list[Any],
) -> None:
    builder.new_page("目录", show_header=False, show_footer=False)
    builder.add_heading("目录")
    items = toc_items or chapters
    if not items:
        builder.add_text("暂无章节。")
        return
    for fallback_index, raw_item in enumerate(items, start=1):
        item = _dict(raw_item)
        index = _int(item.get("index"), fallback_index)
        title = _str(item.get("title")) or f"第{index}章"
        source_count = _int(item.get("source_count"), 0)
        suffix = f"（{source_count} 条讲述来源）" if source_count else ""
        builder.add_text(
            f"{index}. {title}{suffix}",
            line_height=builder.layout.line_height + 2,
            max_units=builder.layout.max_line_units - 1,
        )


def _add_chapters(builder: PDFBookBuilder, chapters: list[Any]) -> None:
    for fallback_index, raw_chapter in enumerate(chapters, start=1):
        chapter = _dict(raw_chapter)
        title = _chapter_title(chapter, fallback_index)
        label = _chapter_label(chapter, fallback_index)
        builder.new_page(
            title,
            show_header=False,
            show_footer=False,
            body_numbering=True,
            continuation_show_header=True,
            continuation_show_footer=True,
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
            builder.add_text(intro, spacing_after=4)

        for raw_quote in _list(chapter.get("pull_quotes"))[:2]:
            quote = _str(_dict(raw_quote).get("text"))
            if quote:
                builder.add_text(
                    f"“{quote}”",
                    font_size=builder.layout.small_font_size + 1,
                    line_height=builder.layout.line_height,
                    max_units=max(12, builder.layout.max_line_units - 3),
                    indent=8,
                    spacing_after=6,
                )
                break

        builder.add_spacer(0.5)

        content = _str(chapter.get("content"))
        if content:
            builder.add_text(content)

        source_count = len(_list(chapter.get("source_refs")))
        if source_count:
            builder.add_spacer(0.75)
            builder.add_text(
                f"本章整理自 {source_count} 条讲述来源。",
                font_size=builder.layout.small_font_size,
                line_height=builder.layout.line_height,
            )


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


def _add_back_matter(builder: PDFBookBuilder, back_matter: dict[str, Any], *, include_source_appendix: bool) -> None:
    section_order = [
        ("afterword", "后记"),
        ("interview_records", "讲述记录"),
        ("version_note", "版本说明"),
    ]
    for key, fallback_title in section_order:
        section = _dict(back_matter.get(key))
        content = _section_content(section)
        if content:
            _add_section_page(
                builder,
                section_title=_section_title(section, fallback_title),
                content=content,
                show_header=True,
                show_footer=True,
                body_numbering=True,
            )
    _add_unconfirmed_facts(builder, _list(back_matter.get("unconfirmed_facts")))
    if include_source_appendix:
        section = _dict(back_matter.get("source_appendix"))
        content = _section_content(section)
        if content:
            _add_section_page(
                builder,
                section_title=_section_title(section, "章节来源"),
                content=content,
                show_header=True,
                show_footer=True,
                body_numbering=True,
            )


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
        builder.add_text(content.strip() or title)
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

    builder = PDFBookBuilder(title=book_title, layout=layout)
    _add_cover_page(builder, title=book_title, subtitle=subtitle, prepared_by=prepared_by)
    _add_front_matter(builder, front_matter, title=book_title, subtitle=subtitle)
    _add_table_of_contents(
        builder,
        toc_items=_list(manuscript.get("table_of_contents")),
        chapters=chapters,
    )
    _add_chapters(builder, chapters)
    _add_back_matter(builder, back_matter, include_source_appendix=include_source_appendix)
    return builder.pages


def _render_manuscript_pdf(
    *,
    title: str,
    content: str,
    manuscript: dict[str, Any],
    layout: PDFLayout,
    show_source_appendix: bool | None = None,
) -> bytes:
    pages = _build_manuscript_pdf_pages(
        title=title,
        content=content,
        manuscript=manuscript,
        layout=layout,
        show_source_appendix=show_source_appendix,
    )
    return _render_pdf_pages(pages, layout)


def _book_export_filename(
    title: str,
    version: int,
    extension: str,
    layout: MemoirBookOutputExportLayout,
) -> str:
    suffix = PDF_LAYOUTS[layout].filename_suffix if extension == "pdf" else ""
    parts = [title, f"全书第{version}版", suffix]
    return safe_filename("-".join(part for part in parts if part), extension)


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
    if export_format == MemoirBookOutputExportFormat.PDF:
        pdf_layout = PDF_LAYOUTS.get(layout, PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD])
        return ExportFile(
            filename=_book_export_filename(title, version, "pdf", layout),
            media_type="application/pdf",
            content=_render_manuscript_pdf(
                title=title,
                content=content,
                manuscript=manuscript or {},
                layout=pdf_layout,
                show_source_appendix=show_source_appendix,
            ),
        )
    return ExportFile(
        filename=_book_export_filename(title, version, "txt", MemoirBookOutputExportLayout.STANDARD),
        media_type="text/plain; charset=utf-8",
        content=content + "\n",
    )
