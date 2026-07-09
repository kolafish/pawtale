from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.services.file_exports import ExportFile, safe_filename
from app.services.memoir.book_manuscript_cleanup import (
    formal_book_title,
    sanitize_manuscript_payload,
)

from app.services.memoir.book_output_pdf_renderer import (
    PDF_LAYOUTS,
    PDF_SERIF_BOLD_FONT_NAME,
    PDF_SERIF_FONT_NAME,
    PDFBookBuilder,
    PDFColor,
    PDFLayout,
    PDFPage,
    PDFTocEntry,
    MemoirBookOutputExportLayout,
    _char_units,
    _render_pdf_pages,
    _text_units,
    _truncate_units,
    _wrap_pdf_line,
)


PDF_BOOK_COVER_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "book_covers"


@dataclass(frozen=True)
class PDFCoverStyle:
    background: PDFColor
    text: PDFColor
    line: PDFColor
    subtle_line: PDFColor
    image_layout: str = ""
    cover_background_image: Path | None = None
    back_cover_background_image: Path | None = None


PDF_COVER_STYLES: dict[str, PDFCoverStyle] = {
    "classic_plain": PDFCoverStyle(
        background=(0.96, 0.92, 0.86),
        text=(0.22, 0.15, 0.12),
        line=(0.78, 0.70, 0.61),
        subtle_line=(0.88, 0.82, 0.75),
    ),
    "warm_gray": PDFCoverStyle(
        background=(0.95, 0.94, 0.91),
        text=(0.20, 0.19, 0.17),
        line=(0.73, 0.70, 0.66),
        subtle_line=(0.84, 0.82, 0.78),
    ),
    "dark_album": PDFCoverStyle(
        background=(0.24, 0.22, 0.20),
        text=(0.95, 0.92, 0.86),
        line=(0.61, 0.55, 0.46),
        subtle_line=(0.42, 0.38, 0.32),
    ),
    "moonlit_courtyard": PDFCoverStyle(
        background=(0.95, 0.90, 0.80),
        text=(0.22, 0.13, 0.06),
        line=(0.34, 0.25, 0.16),
        subtle_line=(0.62, 0.54, 0.43),
        image_layout="watercolor",
        cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "moonlit_courtyard_cover.jpg",
        back_cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "moonlit_courtyard_back_cover.jpg",
    ),
    "navy_gilt": PDFCoverStyle(
        background=(0.02, 0.05, 0.08),
        text=(0.86, 0.65, 0.34),
        line=(0.72, 0.50, 0.24),
        subtle_line=(0.55, 0.41, 0.23),
        image_layout="navy_gilt",
        cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "navy_gilt_cover.jpg",
        back_cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "navy_gilt_back_cover.jpg",
    ),
}


class MemoirBookOutputExportFormat(StrEnum):
    TXT = "txt"
    PDF = "pdf"

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


def _today_month_text() -> str:
    today = datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date()
    return f"{today.year} 年 {today.month} 月"


def _cover_style(style: str) -> PDFCoverStyle:
    return PDF_COVER_STYLES.get(style, PDF_COVER_STYLES["classic_plain"])


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


def _add_watercolor_cover_label(builder: PDFBookBuilder, *, style: PDFCoverStyle, y: int) -> None:
    box_width = 96
    box_height = 20
    box_x = (builder.layout.page_width - box_width) / 2
    builder.add_decoration(
        kind="stroke_rect",
        x=box_x,
        y=y - 6,
        width=box_width,
        height=box_height,
        color=style.subtle_line,
        line_width=0.45,
    )
    builder.add_centered_at(
        "家庭回忆录",
        y=y,
        font_size=max(9, builder.layout.small_font_size),
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_FONT_NAME,
        char_spacing=1.8,
    )


def _add_watercolor_separator(builder: PDFBookBuilder, *, style: PDFCoverStyle, y: int, width: int = 116) -> None:
    gap = 18
    left_width = (width - gap) / 2
    center_x = builder.layout.page_width / 2
    builder.add_decoration(
        kind="line",
        x=center_x - width / 2,
        y=y,
        width=left_width,
        height=0,
        color=style.subtle_line,
        line_width=0.35,
    )
    builder.add_decoration(
        kind="line",
        x=center_x + gap / 2,
        y=y,
        width=left_width,
        height=0,
        color=style.subtle_line,
        line_width=0.35,
    )
    builder.add_centered_at(
        "◇",
        y=y - 5,
        font_size=max(7, builder.layout.small_font_size - 2),
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        color=style.subtle_line,
    )


def _add_watercolor_cover_page(
    builder: PDFBookBuilder,
    *,
    title: str,
    subtitle: str,
    narrator_name: str,
    prepared_by: str,
    style: PDFCoverStyle,
) -> None:
    builder.new_page(
        show_header=False,
        show_footer=False,
        background_color=style.background,
        background_image_path=style.cover_background_image,
        text_color=style.text,
    )
    layout = builder.layout
    _add_watercolor_cover_label(builder, style=style, y=layout.page_height - 108)
    title_next_y = builder.add_centered_at(
        title,
        y=layout.page_height - 218,
        font_size=layout.title_font_size + 15,
        line_height=layout.line_height + 28,
        max_units=max(6, layout.max_line_units - 12),
        color=style.text,
        font_name=PDF_SERIF_FONT_NAME,
        char_spacing=2.1,
        spacing_after=12,
    )
    separator_y = min(title_next_y + 24, layout.page_height - 250)
    _add_watercolor_separator(builder, style=style, y=separator_y, width=132)
    if subtitle:
        builder.add_centered_at(
            subtitle,
            y=separator_y - 31,
            font_size=layout.font_size + 4,
            line_height=layout.line_height + 6,
            max_units=max(10, layout.max_line_units - 6),
            color=style.text,
            font_name=PDF_SERIF_FONT_NAME,
            char_spacing=2.4,
        )

    credit_y = layout.margin_bottom + 64
    if narrator_name:
        builder.add_centered_at(
            f"口述：{narrator_name}",
            y=credit_y + 20,
            font_size=layout.small_font_size + 1,
            line_height=layout.line_height,
            max_units=layout.max_line_units,
            color=style.text,
            font_name=PDF_SERIF_FONT_NAME,
            char_spacing=0.8,
        )
    builder.add_centered_at(
        f"整理：{prepared_by or '家人'}",
        y=credit_y,
        font_size=layout.small_font_size + 1,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_FONT_NAME,
        char_spacing=0.8,
    )
    _add_watercolor_separator(builder, style=style, y=layout.margin_bottom - 7, width=86)
    builder.add_centered_at(
        _today_date_text()[:4],
        y=layout.margin_bottom - 37,
        font_size=layout.small_font_size + 3,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_FONT_NAME,
        char_spacing=3.0,
    )


def _add_navy_gilt_separator(builder: PDFBookBuilder, *, style: PDFCoverStyle, y: int, width: int = 160) -> None:
    gap = 30
    left_width = (width - gap) / 2
    center_x = builder.layout.page_width / 2
    builder.add_decoration(
        kind="line",
        x=center_x - width / 2,
        y=y,
        width=left_width,
        height=0,
        color=style.line,
        line_width=0.45,
    )
    builder.add_decoration(
        kind="line",
        x=center_x + gap / 2,
        y=y,
        width=left_width,
        height=0,
        color=style.line,
        line_width=0.45,
    )
    builder.add_centered_at(
        "◇",
        y=y - 5,
        font_size=max(8, builder.layout.small_font_size - 1),
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        color=style.text,
    )


def _add_navy_gilt_cover_page(
    builder: PDFBookBuilder,
    *,
    title: str,
    subtitle: str,
    narrator_name: str,
    prepared_by: str,
    style: PDFCoverStyle,
) -> None:
    builder.new_page(
        show_header=False,
        show_footer=False,
        background_color=style.background,
        background_image_path=style.cover_background_image,
        text_color=style.text,
    )
    layout = builder.layout
    builder.add_centered_at(
        "家庭回忆录",
        y=layout.page_height - 176,
        font_size=layout.small_font_size + 5,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_BOLD_FONT_NAME,
        char_spacing=2.8,
    )
    title_next_y = builder.add_centered_at(
        title,
        y=layout.page_height - 248,
        font_size=layout.title_font_size + 28,
        line_height=layout.line_height + 38,
        max_units=max(5, layout.max_line_units - 15),
        color=style.text,
        font_name=PDF_SERIF_BOLD_FONT_NAME,
        char_spacing=4.6,
        spacing_after=8,
    )
    _add_navy_gilt_separator(builder, style=style, y=title_next_y + 20, width=190)
    if subtitle:
        builder.add_centered_at(
            subtitle,
            y=title_next_y - 22,
            font_size=layout.font_size + 5,
            line_height=layout.line_height + 7,
            max_units=max(10, layout.max_line_units - 6),
            color=style.text,
            font_name=PDF_SERIF_BOLD_FONT_NAME,
            char_spacing=3.1,
        )

    credit_y = layout.margin_bottom + 74
    if narrator_name:
        builder.add_centered_at(
            f"口述：{narrator_name}",
            y=credit_y + 24,
            font_size=layout.small_font_size + 2,
            line_height=layout.line_height,
            max_units=layout.max_line_units,
            color=style.text,
            font_name=PDF_SERIF_BOLD_FONT_NAME,
            char_spacing=1.3,
        )
    builder.add_centered_at(
        f"整理：{prepared_by or '家人'}",
        y=credit_y,
        font_size=layout.small_font_size + 2,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_BOLD_FONT_NAME,
        char_spacing=1.3,
    )
    _add_navy_gilt_separator(builder, style=style, y=layout.margin_bottom + 2, width=92)
    builder.add_centered_at(
        _today_date_text()[:4],
        y=layout.margin_bottom - 29,
        font_size=layout.small_font_size + 4,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_BOLD_FONT_NAME,
        char_spacing=6.0,
    )


def _add_cover_page(
    builder: PDFBookBuilder,
    *,
    title: str,
    subtitle: str,
    narrator_name: str,
    prepared_by: str,
    cover_style: str,
) -> None:
    style = _cover_style(cover_style)
    if style.cover_background_image and style.image_layout == "navy_gilt":
        _add_navy_gilt_cover_page(
            builder,
            title=title,
            subtitle=subtitle,
            narrator_name=narrator_name,
            prepared_by=prepared_by,
            style=style,
        )
        return
    if style.cover_background_image:
        _add_watercolor_cover_page(
            builder,
            title=title,
            subtitle=subtitle,
            narrator_name=narrator_name,
            prepared_by=prepared_by,
            style=style,
        )
        return

    line_color = style.line
    subtle_line_color = style.subtle_line
    builder.new_page(
        show_header=False,
        show_footer=False,
        background_color=style.background,
        text_color=style.text,
    )
    builder.add_decoration(
        kind="line",
        x=builder.layout.margin_x,
        y=builder.layout.page_height - builder.layout.margin_top + 8,
        width=builder.layout.page_width - builder.layout.margin_x * 2,
        height=0,
        color=line_color,
        line_width=0.7,
    )
    builder.add_decoration(
        kind="line",
        x=builder.layout.margin_x,
        y=builder.layout.margin_bottom + 20,
        width=builder.layout.page_width - builder.layout.margin_x * 2,
        height=0,
        color=line_color,
        line_width=0.7,
    )
    builder.add_decoration(
        kind="line",
        x=builder.layout.page_width / 2 - 24,
        y=builder.layout.page_height / 2 - 35,
        width=48,
        height=0,
        color=subtle_line_color,
        line_width=0.35,
    )
    builder.add_spacer(2.7)
    builder.add_centered(
        "家庭回忆录",
        font_size=max(8, builder.layout.small_font_size - 1),
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        spacing_after=54,
    )
    builder.add_centered(
        title,
        font_size=builder.layout.title_font_size + 6,
        line_height=builder.layout.line_height + 16,
        max_units=max(8, builder.layout.max_line_units - 6),
        spacing_after=18,
    )
    if subtitle:
        builder.add_centered(
            subtitle,
            font_size=builder.layout.font_size,
            line_height=builder.layout.line_height + 4,
            max_units=builder.layout.max_line_units - 2,
            spacing_after=34,
        )
    builder.y = max(builder.layout.margin_bottom + 92, builder.y - 96)
    if narrator_name:
        builder.add_centered(
            f"口述：{narrator_name}",
            font_size=builder.layout.small_font_size,
            line_height=builder.layout.line_height,
            max_units=builder.layout.max_line_units,
            spacing_after=2,
        )
    builder.add_centered(
        f"整理：{prepared_by or '家人'}",
        font_size=builder.layout.small_font_size,
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        spacing_after=2,
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


def _chapter_pull_quote(chapter: dict[str, Any]) -> str:
    for raw_quote in _list(chapter.get("pull_quotes"))[:2]:
        quote = _str(_dict(raw_quote).get("text"))
        if quote:
            return quote
    return ""


def _quote_overlap_key(text: str) -> str:
    return text.strip().strip("“”\"'「」").strip("，。！？；：、,.!?;: ")


def _without_leading_quote_overlap(content: str, quote: str) -> str:
    quote_key = _quote_overlap_key(quote)
    if len(quote_key) < 8:
        return content
    stripped = content.lstrip()
    content_key = _quote_overlap_key(stripped[: max(len(quote_key) + 4, 16)])
    if not content_key.startswith(quote_key):
        return content
    remainder = stripped[len(quote_key) :].lstrip("，。！？；：、,.!?;: \n")
    return remainder or content


def _without_embedded_quote_overlap(text: str, quote: str) -> str:
    quote_key = _quote_overlap_key(quote)
    if len(quote_key) < 8:
        return text
    cleaned = text
    for suffix in ("。", "！", "？", "，", "；", ""):
        cleaned = cleaned.replace(f"{quote_key}{suffix}", "")
    return cleaned.strip()


def _illustration_file_path(illustration: dict[str, Any]) -> Path | None:
    storage_path = _str(illustration.get("storage_path"))
    if not storage_path:
        return None
    path = Path(storage_path)
    resolved = path.resolve() if path.is_absolute() else (DATA_DIR / path).resolve()
    data_root = DATA_DIR.resolve()
    if data_root not in resolved.parents and resolved != data_root:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return resolved


def _illustration_caption(illustration: dict[str, Any]) -> str:
    caption = _str(illustration.get("caption"))
    disclosure = _str(illustration.get("disclosure"))
    if caption and disclosure and disclosure not in caption:
        return f"{caption}\n{disclosure}"
    return caption or disclosure


def _add_illustration(builder: PDFBookBuilder, illustration: dict[str, Any]) -> None:
    path = _illustration_file_path(illustration)
    if not path:
        return
    builder.add_image(
        path,
        caption=_illustration_caption(illustration),
        display_size=_str(illustration.get("display_size")) or "full_width",
    )


def _add_chapter_illustrations(builder: PDFBookBuilder, chapter: dict[str, Any]) -> None:
    illustrations = [
        _dict(item)
        for item in _list(chapter.get("illustrations"))
        if _dict(item).get("placement") in {"chapter_opening", "chapter_inline"}
    ]
    if not illustrations:
        return
    builder.add_spacer(0.25)
    for illustration in illustrations:
        _add_illustration(builder, illustration)


def _add_chapters(builder: PDFBookBuilder, chapters: list[Any]) -> None:
    for fallback_index, raw_chapter in enumerate(chapters, start=1):
        chapter = _dict(raw_chapter)
        title = _chapter_title(chapter, fallback_index)
        label = _chapter_label(chapter, fallback_index)
        anchor = _chapter_anchor(fallback_index)
        builder.new_page(
            title,
            show_header=False,
            show_footer=True,
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

        quote = _chapter_pull_quote(chapter)
        if quote:
            builder.add_quote(quote, spacing_after=6)

        intro = _str(chapter.get("intro"))
        intro = _without_embedded_quote_overlap(intro, quote)
        if intro:
            builder.add_body_text(intro, spacing_after=4)

        _add_chapter_illustrations(builder, chapter)
        builder.add_spacer(0.5)

        content = _without_leading_quote_overlap(_str(chapter.get("content")), quote)
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


def _add_image_appendix(builder: PDFBookBuilder, illustrations: list[Any]) -> None:
    images = [_dict(item) for item in illustrations if _dict(item)]
    if not images:
        return
    builder.new_page("插图", show_header=True, show_footer=True, body_numbering=True)
    builder.add_heading("插图")
    for index, illustration in enumerate(images, start=1):
        caption = _illustration_caption(illustration)
        if caption:
            illustration = {**illustration, "caption": f"{index}. {caption}"}
        _add_illustration(builder, illustration)


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


def _watercolor_back_cover_quote_lines(quote: str, *, max_units: int) -> list[str]:
    cleaned = quote.strip().strip("“”\"'「」")
    if not cleaned:
        return ["愿这些记忆被家人慢慢翻看。"]
    if "，" in cleaned:
        parts = cleaned.split("，")
        lines = [
            f"{part}，" if index < len(parts) - 1 else part
            for index, part in enumerate(parts)
            if part
        ]
        if lines and all(_text_units(line) <= max_units for line in lines):
            return lines
    return _wrap_pdf_line(cleaned, max_units)


def _add_watercolor_back_cover(builder: PDFBookBuilder, *, quote: str, style: PDFCoverStyle) -> None:
    builder.new_page(
        show_header=False,
        show_footer=False,
        background_color=style.background,
        background_image_path=style.back_cover_background_image or style.cover_background_image,
        text_color=style.text,
    )
    layout = builder.layout
    _add_watercolor_cover_label(builder, style=style, y=layout.page_height - 108)
    quote_next_y = layout.page_height - 214
    for line in _watercolor_back_cover_quote_lines(quote, max_units=max(13, layout.max_line_units - 10)):
        quote_next_y = builder.add_centered_at(
            line,
            y=quote_next_y,
            font_size=layout.font_size + 6,
            line_height=layout.line_height + 17,
            max_units=layout.max_line_units,
            color=style.text,
            font_name=PDF_SERIF_FONT_NAME,
            char_spacing=1.3,
        )
    quote_next_y -= 28
    _add_watercolor_separator(builder, style=style, y=quote_next_y + 8, width=92)
    builder.add_centered_at(
        "本书根据口述访谈整理",
        y=quote_next_y - 32,
        font_size=layout.small_font_size + 1,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_FONT_NAME,
        char_spacing=0.7,
    )
    builder.add_centered_at(
        f"整理于 {_today_month_text()}",
        y=quote_next_y - 52,
        font_size=layout.small_font_size + 1,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_FONT_NAME,
        char_spacing=1.1,
    )


def _add_navy_gilt_back_cover(builder: PDFBookBuilder, *, quote: str, style: PDFCoverStyle) -> None:
    builder.new_page(
        show_header=False,
        show_footer=False,
        background_color=style.background,
        background_image_path=style.back_cover_background_image or style.cover_background_image,
        text_color=style.text,
    )
    layout = builder.layout
    quote_next_y = layout.page_height - 154
    for line in _watercolor_back_cover_quote_lines(quote, max_units=max(13, layout.max_line_units - 10)):
        quote_next_y = builder.add_centered_at(
            line,
            y=quote_next_y,
            font_size=layout.font_size + 10,
            line_height=layout.line_height + 20,
            max_units=layout.max_line_units,
            color=style.text,
            font_name=PDF_SERIF_BOLD_FONT_NAME,
            char_spacing=2.5,
        )
    builder.add_centered_at(
        "本书根据口述访谈整理",
        y=layout.margin_bottom + 118,
        font_size=layout.small_font_size + 1,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_BOLD_FONT_NAME,
        char_spacing=1.2,
    )
    builder.add_centered_at(
        f"整理于 {_today_month_text()}",
        y=layout.margin_bottom + 94,
        font_size=layout.small_font_size + 1,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_BOLD_FONT_NAME,
        char_spacing=1.8,
    )


def _add_back_cover(builder: PDFBookBuilder, *, quote: str, cover_style: str) -> None:
    style = _cover_style(cover_style)
    if style.back_cover_background_image and style.image_layout == "navy_gilt":
        _add_navy_gilt_back_cover(builder, quote=quote, style=style)
        return
    if style.back_cover_background_image:
        _add_watercolor_back_cover(builder, quote=quote, style=style)
        return

    subtle_line_color = style.subtle_line
    builder.new_page(
        show_header=False,
        show_footer=False,
        background_color=style.background,
        text_color=style.text,
    )
    builder.add_decoration(
        kind="line",
        x=builder.layout.margin_x,
        y=builder.layout.page_height - builder.layout.margin_top + 8,
        width=builder.layout.page_width - builder.layout.margin_x * 2,
        height=0,
        color=style.line,
        line_width=0.7,
    )
    builder.add_decoration(
        kind="line",
        x=builder.layout.page_width / 2 - 24,
        y=builder.layout.page_height / 2 + 56,
        width=48,
        height=0,
        color=subtle_line_color,
        line_width=0.35,
    )
    builder.add_decoration(
        kind="line",
        x=builder.layout.margin_x,
        y=builder.layout.margin_bottom + 20,
        width=builder.layout.page_width - builder.layout.margin_x * 2,
        height=0,
        color=style.line,
        line_width=0.7,
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
        f"整理于 {_today_month_text()}",
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
    narrator_name: str,
    prepared_by: str,
    back_cover_quote: str,
    cover_style: str,
    front_matter: list[Any],
    chapters: list[Any],
    image_appendix: list[Any],
    back_matter: dict[str, Any],
    include_source_appendix: bool,
    toc_entries: list[PDFTocEntry],
    layout: PDFLayout,
) -> list[PDFPage]:
    builder = PDFBookBuilder(title=book_title, layout=layout)
    _add_cover_page(
        builder,
        title=book_title,
        subtitle=subtitle,
        narrator_name=narrator_name,
        prepared_by=prepared_by,
        cover_style=cover_style,
    )
    _add_front_matter(builder, front_matter, title=book_title, subtitle=subtitle)
    _add_table_of_contents(builder, entries=toc_entries)
    _add_chapters(builder, chapters)
    _add_image_appendix(builder, image_appendix)
    _add_back_matter(builder, back_matter, include_source_appendix=include_source_appendix)
    _add_back_cover(builder, quote=back_cover_quote, cover_style=cover_style)
    return builder.pages


def _build_manuscript_pdf_pages(
    *,
    title: str,
    content: str,
    manuscript: dict[str, Any],
    layout: PDFLayout,
    show_source_appendix: bool | None = None,
) -> list[PDFPage]:
    manuscript = sanitize_manuscript_payload(title=title, manuscript=manuscript)
    profile = _dict(manuscript.get("profile"))
    chapters = _list(manuscript.get("chapters"))
    image_appendix = _list(manuscript.get("image_appendix"))
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
    narrator_name = _str(profile.get("narrator_name"))
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
    cover_style = _str(settings.get("cover_style")) or "classic_plain"

    toc_items = _list(manuscript.get("table_of_contents"))
    initial_entries = _chapter_toc_entries(toc_items=toc_items, chapters=chapters)
    initial_pages = _build_structured_manuscript_pdf_pages(
        book_title=book_title,
        subtitle=subtitle,
        narrator_name=narrator_name,
        prepared_by=prepared_by,
        back_cover_quote=back_cover_quote,
        cover_style=cover_style,
        front_matter=front_matter,
        chapters=chapters,
        image_appendix=image_appendix,
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
        narrator_name=narrator_name,
        prepared_by=prepared_by,
        back_cover_quote=back_cover_quote,
        cover_style=cover_style,
        front_matter=front_matter,
        chapters=chapters,
        image_appendix=image_appendix,
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
    manuscript = sanitize_manuscript_payload(title=title, manuscript=manuscript or {})
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
    return formal_book_title(raw_title)


def _export_title(title: str, manuscript: dict[str, Any] | None) -> str:
    sanitized = sanitize_manuscript_payload(title=title, manuscript=manuscript or {})
    profile = _dict(sanitized.get("profile"))
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
