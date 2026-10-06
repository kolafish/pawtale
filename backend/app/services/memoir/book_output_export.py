from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.services.file_exports import ExportFile, safe_filename
from app.services.memoir.book_manuscript_cleanup import sanitize_manuscript_payload
from app.services.memoir.book_output_pdf_renderer import (
    PDF_LAYOUTS,
    PDF_HERITAGE_ACCENT,
    PDF_MODERN_ACCENT,
    PDF_SANS_FONT_NAME,
    PDF_SERIF_BOLD_FONT_NAME,
    PDF_SERIF_FONT_NAME,
    PDF_WARM_ACCENT,
    PDFBookBuilder,
    PDFColor,
    PDFLayout,
    PDFImageCommand,
    PDFPage,
    PDFTocEntry,
    MemoirBookOutputExportLayout,
    configured_pdf_layout,
    _render_pdf_pages,
    _text_units,
    _wrap_pdf_line,
)
from app.services.memoir.branding import BRAND_ATTRIBUTION, brand_lockup_file


PDF_BOOK_COVER_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "book_covers"
PDF_BOOK_ORNAMENT_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "book_ornaments"


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
    "heritage_classic": PDFCoverStyle(
        background=(0.93, 0.88, 0.78),
        text=(0.26, 0.19, 0.13),
        line=PDF_HERITAGE_ACCENT,
        subtle_line=(0.72, 0.61, 0.48),
        image_layout="artwork",
        cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "heritage_classic_cover.jpg",
        back_cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "heritage_classic_back_cover.jpg",
    ),
    "warm_journal": PDFCoverStyle(
        background=(0.96, 0.90, 0.83),
        text=(0.32, 0.20, 0.16),
        line=PDF_WARM_ACCENT,
        subtle_line=(0.75, 0.58, 0.47),
        image_layout="artwork",
        cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "warm_journal_cover.jpg",
        back_cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "warm_journal_back_cover.jpg",
    ),
    "modern_minimal": PDFCoverStyle(
        background=(0.08, 0.13, 0.15),
        text=(0.93, 0.94, 0.90),
        line=(0.68, 0.79, 0.78),
        subtle_line=(0.49, 0.63, 0.63),
        image_layout="artwork",
        cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "modern_minimal_cover.jpg",
        back_cover_background_image=PDF_BOOK_COVER_ASSET_DIR / "modern_minimal_back_cover.jpg",
    ),
}


@dataclass(frozen=True)
class PDFArtworkCoverTypography:
    font_name: str
    title_size_delta: int
    title_char_spacing: float


PDF_ARTWORK_COVER_TYPOGRAPHY = {
    "heritage_classic": PDFArtworkCoverTypography(PDF_SERIF_BOLD_FONT_NAME, 14, 1.2),
    "warm_journal": PDFArtworkCoverTypography(PDF_SERIF_FONT_NAME, 15, 1.0),
    "modern_minimal": PDFArtworkCoverTypography(PDF_SANS_FONT_NAME, 18, 1.2),
}


class MemoirBookOutputExportFormat(StrEnum):
    TXT = "txt"
    PDF = "pdf"


EXPORT_DATE: ContextVar[str | None] = ContextVar("memoir_export_date", default=None)

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
    return EXPORT_DATE.get() or datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()


def _today_month_text() -> str:
    today = datetime.fromisoformat(_today_date_text()).date()
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
    compact: bool = False,
    force_right_page: bool = False,
) -> None:
    if not content and not (heading or section_title):
        return
    if (
        force_right_page
        and not builder.layout.reading_edition
        and (len(builder.pages) + 1) % 2 == 0
    ):
        builder.new_page(show_header=False, show_footer=False)
    builder.new_page(
        section_title,
        show_header=show_header,
        show_footer=show_footer,
        body_numbering=body_numbering,
    )
    page_heading = heading or section_title
    if compact:
        builder.add_text(
            page_heading,
            font_size=builder.layout.small_font_size + 2,
            line_height=builder.layout.line_height,
            max_units=builder.layout.max_line_units,
            spacing_after=8,
            font_name=PDF_SERIF_BOLD_FONT_NAME,
        )
    else:
        builder.add_heading(page_heading)
    if content and compact:
        compact_font_size = builder.layout.small_font_size + 1
        builder.add_text(
            content,
            font_size=compact_font_size,
            line_height=max(18, builder.layout.line_height - 3),
            max_units=builder.layout.max_line_units + 2,
            first_line_indent=compact_font_size * 2,
            paragraph_spacing_after=4,
            min_start_lines=1,
            keep_last_lines=1,
        )
    elif content:
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


def _add_artwork_cover_page(
    builder: PDFBookBuilder,
    *,
    title: str,
    subtitle: str,
    narrator_name: str,
    prepared_by: str,
    style: PDFCoverStyle,
    cover_style: str,
) -> None:
    typography = PDF_ARTWORK_COVER_TYPOGRAPHY[cover_style]
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
        y=layout.page_height - 137,
        font_size=layout.small_font_size + 3,
        line_height=layout.line_height,
        color=style.text,
        font_name=typography.font_name,
        char_spacing=2.1,
    )

    # Keep the generated artwork text-free. Fit the user's actual title into its
    # clear center, so a longer title does not collide with the credits below.
    long_warm_title = cover_style == "warm_journal" and _text_units(title) > 28
    title_y = layout.page_height - (183 if long_warm_title else 203)
    title_size = layout.title_font_size + typography.title_size_delta
    title_margin = 80 if long_warm_title else max(layout.margin_x, 56)
    while True:
        title_units = max(
            5,
            int(
                (layout.page_width - 2 * title_margin)
                / (title_size + typography.title_char_spacing)
            ),
        )
        title_lines = _wrap_pdf_line(title, title_units)
        if len(title_lines) * (title_size + 10) <= 185 or title_size <= 18:
            break
        title_size -= 2
    title_next_y = builder.add_centered_at(
        title,
        y=title_y,
        font_size=title_size,
        line_height=title_size + 10,
        max_units=title_units,
        spacing_after=11,
        color=style.text,
        font_name=typography.font_name,
        char_spacing=typography.title_char_spacing,
        x_offset=-16 if long_warm_title else 0,
    )
    credit_y = layout.margin_bottom + 72
    if subtitle:
        subtitle_size = layout.font_size + 2
        subtitle_line_height = subtitle_size + 8
        subtitle_units = max(
            9,
            int((layout.page_width - 2 * max(layout.margin_x, 56)) / (subtitle_size + 1)),
        )
        subtitle_lines = _wrap_pdf_line(subtitle, subtitle_units)
        available_lines = max(
            0, int((title_next_y - credit_y - 70) // subtitle_line_height) + 1
        )
        if len(subtitle_lines) > available_lines and available_lines:
            subtitle_lines = subtitle_lines[:available_lines]
            subtitle_lines[-1] = subtitle_lines[-1].rstrip("，。 ")[:-1] + "…"
        for line in subtitle_lines[:available_lines]:
            title_next_y = builder.add_centered_at(
                line,
                y=title_next_y,
                font_size=subtitle_size,
                line_height=subtitle_line_height,
                max_units=subtitle_units,
                color=style.text,
                font_name=typography.font_name,
                char_spacing=0.8,
                x_offset=-12 if long_warm_title else 0,
            )

    builder.add_decoration(
        kind="line",
        x=layout.page_width / 2 - 35,
        y=credit_y + 54,
        width=70,
        height=0,
        color=style.line,
        line_width=0.5,
    )
    if narrator_name:
        builder.add_centered_at(
            f"口述：{narrator_name}",
            y=credit_y + 23,
            font_size=layout.small_font_size + 1,
            line_height=layout.line_height,
            color=style.text,
            font_name=typography.font_name,
        )
    builder.add_centered_at(
        f"整理：{prepared_by or '家人'}",
        y=credit_y,
        font_size=layout.small_font_size + 1,
        line_height=layout.line_height,
        color=style.text,
        font_name=typography.font_name,
    )
    builder.add_centered_at(
        _today_date_text()[:4],
        y=layout.margin_bottom + 22,
        font_size=layout.small_font_size,
        line_height=layout.line_height,
        color=style.text,
        font_name=typography.font_name,
        char_spacing=1.8,
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
    if style.cover_background_image and style.image_layout == "artwork":
        _add_artwork_cover_page(
            builder,
            title=title,
            subtitle=subtitle,
            narrator_name=narrator_name,
            prepared_by=prepared_by,
            style=style,
            cover_style=cover_style,
        )
        return
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
        font_name=PDF_SERIF_BOLD_FONT_NAME,
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
    reading_sections = [
        _dict(section)
        for section in front_matter
        if _str(_dict(section).get("kind")) in {"preface", "generation_note"}
        and _section_enabled(_dict(section))
        and _section_content(_dict(section))
    ]
    reading_front_matter_added = False
    for raw_section in front_matter:
        section = _dict(raw_section)
        if not _section_enabled(section):
            continue
        kind = _str(section.get("kind"))
        if kind == "cover":
            continue
        if builder.layout.reading_edition:
            if kind == "title_page":
                continue
            if kind in {"preface", "generation_note"}:
                if not reading_front_matter_added and reading_sections:
                    builder.new_page("写在前面", show_header=False, show_footer=False)
                    for index, reading_section in enumerate(
                        sorted(reading_sections, key=lambda item: item.get("kind") != "preface")
                    ):
                        heading = _section_title(reading_section, "书前说明")
                        if index == 0:
                            builder.add_heading(heading)
                        else:
                            builder.add_spacer(0.5)
                            builder.add_section_heading(heading)
                        builder.add_body_text(_section_content(reading_section))
                    reading_front_matter_added = True
                continue
        section_title = _section_title(section, "书前说明")
        content = _section_content(section)
        if kind == "title_page":
            _add_title_page(builder, title=title, content=_title_page_content(subtitle, content))
            continue
        _add_section_page(
            builder,
            section_title=section_title,
            content=content,
            compact=(kind == "generation_note" and not builder.layout.reading_edition),
            force_right_page=(kind == "preface" and not builder.layout.reading_edition),
        )


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
    return f"第{_chinese_number(entry.index)}章　{entry.title}"


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
        chapter = (
            _dict(chapters[fallback_index - 1])
            if fallback_index <= len(chapters)
            else {}
        )
        index = _int(item.get("index"), fallback_index)
        title = _str(item.get("title")) or f"第{index}章"
        anchor = _chapter_anchor(fallback_index)
        section_items = _list(item.get("sections")) or _list(
            chapter.get("sections")
        )
        section_titles = tuple(
            section_title
            for raw_section in section_items
            if (section_title := _str(_dict(raw_section).get("title")))
        )
        entries.append(
            PDFTocEntry(
                index=index,
                title=title,
                anchor=anchor,
                page_number=page_numbers.get(anchor),
                section_titles=section_titles,
            )
        )
    return entries


def _add_table_of_contents(
    builder: PDFBookBuilder,
    *,
    entries: list[PDFTocEntry],
) -> None:
    builder.new_page(
        "目录",
        show_header=False,
        show_footer=False,
        continuation_show_header=True,
    )
    builder.add_heading("目录")
    if not entries:
        builder.add_text("暂无章节。")
        return
    for entry in entries:
        builder.add_toc_row(
            label=_toc_line(entry, builder.layout) if entry.page_number is None else f"第{_chinese_number(entry.index)}章　{entry.title}",
            page_text=str(entry.page_number) if entry.page_number is not None else "",
            target_anchor=entry.anchor if entry.page_number is not None else None,
            font_name=PDF_SERIF_BOLD_FONT_NAME,
        )
        for section_index, section_title in enumerate(
            () if builder.layout.reading_edition else entry.section_titles,
            start=1,
        ):
            builder.add_toc_row(
                label=f"{entry.index}.{section_index}　{section_title}",
                page_text="",
                target_anchor=(
                    entry.anchor if entry.page_number is not None else None
                ),
                indent=18,
                font_size=max(builder.layout.small_font_size, builder.layout.font_size - 2),
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


def _add_illustration(builder: PDFBookBuilder, illustration: dict[str, Any]) -> None:
    path = _illustration_file_path(illustration)
    if not path:
        return
    builder.add_image(
        path,
        display_size=_str(illustration.get("display_size")) or "full_width",
    )


def _add_illustrations(
    builder: PDFBookBuilder,
    illustrations: list[dict[str, Any]],
) -> None:
    if not illustrations:
        return
    builder.add_spacer(0.25)
    for illustration in illustrations:
        _add_illustration(builder, illustration)


def _add_chapter_artwork(builder: PDFBookBuilder, *, template: str, centered: bool) -> bool:
    layout = builder.layout
    if builder.y - 32 < layout.margin_bottom:
        return False
    page = builder.current_page
    assert page is not None
    width = 140
    height = 45
    x = (layout.page_width - width) / 2 if centered else builder._content_left()
    page.images.append(
        PDFImageCommand(
            path=PDF_BOOK_ORNAMENT_ASSET_DIR / f"{template}.png",
            x=x,
            y=builder.y - 9,
            width=width,
            height=height,
        )
    )
    builder.y -= 23
    return True


def _add_chapter_opening(builder: PDFBookBuilder, *, title: str, label: str, index: int) -> None:
    layout = builder.layout
    template = layout.manuscript_template
    title_max_units = max(
        6,
        min(
            layout.max_line_units - 4,
            (layout.page_width - 2 * layout.margin_x - layout.inner_margin_delta)
            // layout.title_font_size,
        ),
    )
    long_title = len(_wrap_pdf_line(title, title_max_units)) > 4
    if template == "heritage_classic":
        opening_spacer = (
            1.5 if layout.mirror_body_pages else 2.5
        ) if long_title else (5 if layout.mirror_body_pages else 3)
        builder.add_spacer(opening_spacer)
        builder.add_centered(
            label,
            font_size=layout.small_font_size + 2,
            line_height=layout.line_height,
            max_units=layout.max_line_units,
            spacing_after=11,
            font_name=PDF_SERIF_FONT_NAME,
            char_spacing=1.2,
        )
        builder.add_centered(
            title,
            font_size=layout.title_font_size,
            line_height=layout.line_height + 12,
            max_units=title_max_units,
            spacing_after=20,
            font_name=PDF_SERIF_BOLD_FONT_NAME,
        )
        _add_chapter_artwork(builder, template=template, centered=True)
        return
    if template == "warm_journal":
        opening_spacer = 2 if long_title else (4 if layout.mirror_body_pages else 2.5)
        builder.add_spacer(opening_spacer)
        builder.add_text(
            label, font_size=layout.small_font_size + 1,
            line_height=layout.line_height, spacing_after=9,
            font_name=PDF_SERIF_FONT_NAME, color=PDF_WARM_ACCENT,
        )
        builder.add_text(
            title, font_size=layout.title_font_size,
            line_height=layout.line_height + 12,
            max_units=title_max_units,
            spacing_after=19, font_name=PDF_SERIF_BOLD_FONT_NAME,
            color=(0.27, 0.17, 0.13),
            avoid_single_char_last_line=True,
        )
        _add_chapter_artwork(builder, template=template, centered=False)
        return
    if template == "modern_minimal":
        opening_spacer = 2 if long_title else (4 if layout.mirror_body_pages else 2.5)
        builder.add_spacer(opening_spacer)
        builder.add_text(
            f"{index:02}", font_size=layout.small_font_size + 2,
            line_height=layout.line_height, spacing_after=13,
            font_name=PDF_SANS_FONT_NAME, color=PDF_MODERN_ACCENT,
        )
        builder.add_text(
            title, font_size=layout.title_font_size,
            line_height=layout.line_height + 12,
            max_units=title_max_units,
            spacing_after=19, font_name=PDF_SANS_FONT_NAME,
            color=(0.10, 0.15, 0.17),
            avoid_single_char_last_line=True,
        )
        ornament_x = builder._content_left()
        has_artwork = _add_chapter_artwork(builder, template=template, centered=False)
        builder.add_decoration(
            kind="line", x=ornament_x + (148 if has_artwork else 0),
            y=builder.y + (35 if has_artwork else 8),
            width=layout.page_width - 2 * layout.margin_x - (148 if has_artwork else 0),
            height=0, color=(0.72, 0.78, 0.79), line_width=0.65,
        )
        return

    builder.add_spacer(4.0 if layout.mirror_body_pages else 2.5)
    builder.add_text(
        label,
        font_size=layout.small_font_size + 1,
        line_height=layout.line_height,
        max_units=layout.max_line_units,
        spacing_after=6,
    )
    builder.add_text(
        title,
        font_size=layout.title_font_size,
        line_height=layout.line_height + 12,
        max_units=title_max_units,
        spacing_after=18 if layout.mirror_body_pages else 12,
        font_name=PDF_SERIF_BOLD_FONT_NAME,
        avoid_single_char_last_line=True,
    )


def _add_chapters(builder: PDFBookBuilder, chapters: list[Any]) -> None:
    for fallback_index, raw_chapter in enumerate(chapters, start=1):
        chapter = _dict(raw_chapter)
        title = _chapter_title(chapter, fallback_index)
        label = _chapter_label(chapter, fallback_index)
        anchor = _chapter_anchor(fallback_index)
        if builder.layout.mirror_body_pages and (len(builder.pages) + 1) % 2 == 0:
            builder.new_page(show_header=False, show_footer=False)
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
        _add_chapter_opening(
            builder, title=title, label=label,
            index=_int(chapter.get("index"), fallback_index),
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
            builder.add_quote(
                quote,
                spacing_after=12 if builder.layout.mirror_body_pages else 6,
            )

        illustrations = [
            _dict(item)
            for item in _list(chapter.get("illustrations"))
            if _dict(item).get("placement") in {"chapter_opening", "chapter_inline"}
        ]
        opening_illustrations = [
            illustration
            for illustration in illustrations
            if illustration.get("placement") == "chapter_opening"
        ]
        inline_illustrations = [
            illustration
            for illustration in illustrations
            if illustration.get("placement") == "chapter_inline"
        ]
        _add_illustrations(builder, opening_illustrations)
        builder.add_spacer(0.5)

        sections = [
            _dict(item)
            for item in _list(chapter.get("sections"))
            if _str(_dict(item).get("title"))
            and _str(_dict(item).get("content"))
        ]
        if sections:
            for section_index, section in enumerate(sections):
                builder.add_section_heading(_str(section.get("title")))
                section_content = _str(section.get("content"))
                if section_index == 0:
                    section_content = _without_leading_quote_overlap(
                        section_content,
                        quote,
                    )
                if section_content:
                    builder.add_body_text(section_content, spacing_after=4)
                section_id = _str(section.get("section_id"))
                anchored_illustrations = [
                    illustration
                    for illustration in inline_illustrations
                    if _str(illustration.get("section_id")) == section_id
                ]
                _add_illustrations(builder, anchored_illustrations)
            unanchored_illustrations = [
                illustration
                for illustration in inline_illustrations
                if not _str(illustration.get("section_id"))
                or not any(
                    _str(section.get("section_id"))
                    == _str(illustration.get("section_id"))
                    for section in sections
                )
            ]
            _add_illustrations(builder, unanchored_illustrations)
            continue
        content = _without_leading_quote_overlap(
            _str(chapter.get("content")),
            quote,
        )
        if content:
            builder.add_body_text(content)
        _add_illustrations(builder, inline_illustrations)


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
            font_name=PDF_SERIF_BOLD_FONT_NAME,
        )
        builder.add_body_text(content, spacing_after=4)


def _add_image_appendix(builder: PDFBookBuilder, illustrations: list[Any]) -> None:
    images = [_dict(item) for item in illustrations if _dict(item)]
    if not images:
        return
    builder.new_page("插图", show_header=True, show_footer=True, body_numbering=True)
    builder.add_heading("插图")
    for illustration in images:
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
    if include_source_appendix:
        for key, fallback_title in [
            ("interview_records", "讲述资料"),
            ("version_note", "版本与修订"),
            ("source_appendix", "章节来源"),
        ]:
            section = _dict(back_matter.get(key))
            if _section_enabled(section):
                content = _section_content(section)
                if content:
                    appendix_sections.append((_section_title(section, fallback_title), content))
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


def _add_back_cover_brand_signature(
    builder: PDFBookBuilder, *, style: PDFCoverStyle, y_offset: int = 0
) -> None:
    background_luminance = sum(style.background) / len(style.background)
    variant = "light" if background_luminance < 0.45 else "dark"
    logo_width = 122
    logo_height = 30
    logo_y = builder.layout.margin_bottom + 36 + y_offset
    page = builder.current_page
    assert page is not None
    page.images.append(
        PDFImageCommand(
            path=brand_lockup_file(360, variant=variant),
            x=(builder.layout.page_width - logo_width) / 2,
            y=logo_y,
            width=logo_width,
            height=logo_height,
        )
    )
    builder.add_centered_at(
        BRAND_ATTRIBUTION,
        y=builder.layout.margin_bottom + 19 + y_offset,
        font_size=builder.layout.small_font_size,
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_FONT_NAME,
        char_spacing=0.5,
    )
    builder.add_centered_at(
        f"整理于 {_today_month_text()}",
        y=builder.layout.margin_bottom + y_offset,
        font_size=builder.layout.small_font_size,
        line_height=builder.layout.line_height,
        max_units=builder.layout.max_line_units,
        color=style.text,
        font_name=PDF_SERIF_FONT_NAME,
        char_spacing=0.8,
    )


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
    _add_back_cover_brand_signature(builder, style=style)


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
    _add_back_cover_brand_signature(builder, style=style)


def _add_artwork_back_cover(
    builder: PDFBookBuilder, *, quote: str, style: PDFCoverStyle, cover_style: str
) -> None:
    typography = PDF_ARTWORK_COVER_TYPOGRAPHY[cover_style]
    builder.new_page(
        show_header=False,
        show_footer=False,
        background_color=style.background,
        background_image_path=style.back_cover_background_image,
        text_color=style.text,
    )
    layout = builder.layout
    quote_lines = _watercolor_back_cover_quote_lines(
        quote, max_units=max(14, layout.max_line_units - 4)
    )
    if len(quote_lines) > 10:
        quote_lines = _wrap_pdf_line(
            "".join(quote_lines), max(14, layout.max_line_units - 4)
        )
    quote_size = layout.font_size + (3 if len(quote_lines) <= 6 else 1)
    quote_y = layout.page_height - 160
    quote_line_height = quote_size + 9
    brand_offset = 25 if cover_style == "warm_journal" else 0
    brand_top = layout.margin_bottom + 36 + brand_offset + 30
    max_quote_lines = max(1, int((quote_y - brand_top - 12) // quote_line_height) + 1)
    if len(quote_lines) > max_quote_lines:
        quote_lines = quote_lines[:max_quote_lines]
        last_line = quote_lines[-1].rstrip("，。 ")
        quote_lines[-1] = f"{last_line[:-1]}…"
    for line in quote_lines:
        quote_y = builder.add_centered_at(
            line,
            y=quote_y,
            font_size=quote_size,
            line_height=quote_line_height,
            max_units=layout.max_line_units,
            color=style.text,
            font_name=typography.font_name,
            char_spacing=0.7,
        )
    _add_back_cover_brand_signature(
        builder, style=style, y_offset=brand_offset
    )


def _add_back_cover(builder: PDFBookBuilder, *, quote: str, cover_style: str) -> None:
    style = _cover_style(cover_style)
    if style.back_cover_background_image and style.image_layout == "artwork":
        _add_artwork_back_cover(builder, quote=quote, style=style, cover_style=cover_style)
        return
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
    builder.add_spacer(6)
    if quote:
        builder.add_centered(
            f"“{quote}”",
            font_size=builder.layout.font_size + 1,
            line_height=builder.layout.line_height + 4,
            max_units=max(10, builder.layout.max_line_units - 4),
            spacing_after=28,
        )
    _add_back_cover_brand_signature(builder, style=style)


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
        else not layout.reading_edition and setting_source_appendix is not False
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


def _export_title(title: str, manuscript: dict[str, Any] | None) -> str:
    sanitized = sanitize_manuscript_payload(title=title, manuscript=manuscript or {})
    profile = _dict(sanitized.get("profile"))
    return _str(profile.get("title")) or title.strip() or "家庭回忆录"


def _filename_book_title(title: str) -> str:
    return "家庭回忆录" if title == "家庭回忆录" else f"《{title}》"


def book_export_filename(
    title: str,
    version: int,
    extension: str,
    layout: MemoirBookOutputExportLayout,
) -> str:
    suffix = PDF_LAYOUTS[layout].filename_suffix if extension == "pdf" else ""
    parts = [_filename_book_title(title), suffix, f"v{version}"]
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
        manuscript_profile = _dict(_dict(manuscript).get("profile"))
        manuscript_settings = _dict(_dict(manuscript).get("render_settings"))
        pdf_layout = configured_pdf_layout(
            pdf_layout,
            template=_str(manuscript_profile.get("template")) or "classic_family",
            custom_layout=_dict(manuscript_settings.get("custom_layout")),
        )
        return ExportFile(
            filename=book_export_filename(export_title, version, "pdf", layout),
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
        filename=book_export_filename(export_title, version, "txt", MemoirBookOutputExportLayout.STANDARD),
        media_type="text/plain; charset=utf-8",
        content=content + "\n",
    )
