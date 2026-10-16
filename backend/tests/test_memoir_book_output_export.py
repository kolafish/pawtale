from hashlib import sha256
from typing import get_args

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.schemas.memoir_book import (
    MemoirBookCoverStyle,
    MemoirBookManuscript,
    MemoirBookManuscriptProfile,
    MemoirBookManuscriptTemplate,
)
from app.services.memoir import book_output_export
from app.services.memoir.book_outputs import _cover_style as normalize_book_cover_style
from app.services.memoir.book_output_manuscript import editable_manuscript, stored_manuscript_design
from app.services.memoir.book_output_export import (
    PDF_COVER_STYLES,
    MemoirBookOutputExportFormat,
    _build_manuscript_pdf_pages,
    render_book_output_export,
)
from app.services.memoir.book_output_pdf_renderer import (
    COMMON_CHINESE_UNBREAKABLE_WORDS,
    LINE_END_FORBIDDEN_CHARS,
    LINE_START_FORBIDDEN_CHARS,
    PDF_LAYOUTS,
    PDF_SANS_FONT_NAME,
    PDF_SANS_FONT_PATH,
    PDF_SERIF_BOLD_FONT_PATH,
    PDF_SERIF_BOLD_FONT_NAME,
    PDF_SERIF_FONT_NAME,
    PDFBookBuilder,
    PDFImageCommand,
    PDFLayout,
    MemoirBookOutputExportLayout,
    configured_pdf_layout,
    _pdf_page_commands,
    _register_pdf_font,
    _optimized_pdf_image_source,
    _wrap_pdf_line,
)
EXPECTED_PDF_COVER_STYLES = {
    "classic_plain",
    "warm_gray",
    "dark_album",
    "moonlit_courtyard",
    "navy_gilt",
    "heritage_classic",
    "warm_journal",
    "modern_minimal",
}


def test_pdf_font_assets_use_the_intended_weights():
    bold_face = TTFont("TestMemoirSerifBold", str(PDF_SERIF_BOLD_FONT_PATH)).face
    sans_face = TTFont("TestMemoirSansRegular", str(PDF_SANS_FONT_PATH)).face

    assert bold_face.styleName == b"Bold"
    assert sans_face.styleName == b"Regular"


def test_pdf_renderer_reencodes_png_and_downsamples_to_print_resolution(tmp_path):
    cases = [
        ((1280, 720), (1280, 720)),
        ((2848, 1600), (1350, 758)),
    ]
    for index, (source_size, expected_size) in enumerate(cases):
        image_path = tmp_path / f"illustration-{index}.png"
        Image.new("RGB", source_size, "#6f7f72").save(image_path)

        source = _optimized_pdf_image_source(
            PDFImageCommand(path=image_path, x=0, y=0, width=324, height=220)
        )

        assert isinstance(source, ImageReader)
        assert source.getSize() == expected_size


def test_pdf_renderer_preserves_transparency_for_brand_assets(tmp_path):
    image_path = tmp_path / "transparent-logo.png"
    image = Image.new("RGBA", (320, 80), (0, 0, 0, 0))
    for x in range(40, 280):
        for y in range(20, 60):
            image.putpixel((x, y), (58, 33, 25, 255))
    image.save(image_path)

    source = _optimized_pdf_image_source(
        PDFImageCommand(path=image_path, x=0, y=0, width=120, height=30)
    )

    assert isinstance(source, ImageReader)
    source.getRGBData()
    assert source._dataA is not None


def _sample_manuscript(chapter_content: str, *, cover_style: str | None = None) -> dict:
    render_settings = {"show_source_appendix": True}
    if cover_style:
        render_settings["cover_style"] = cover_style
    return {
        "profile": {
            "title": "外婆回忆录",
            "subtitle": "根据口述访谈整理",
            "narrator_name": "",
            "prepared_by": "家人整理",
        },
        "front_matter": [
            {"kind": "cover", "title": "封面", "content": ""},
            {"kind": "title_page", "title": "扉页", "content": "家人内部保存。"},
            {"kind": "preface", "title": "前言", "content": "这些故事从家人的讲述开始。"},
        ],
        "table_of_contents": [
            {
                "index": 1,
                "title": "外婆家的童年",
                "source_count": 2,
                "sections": [
                    {
                        "section_id": "section-1",
                        "index": 1,
                        "title": "院子里的日子",
                        "char_count": len(chapter_content),
                        "source_count": 2,
                    }
                ],
            }
        ],
        "chapters": [
            {
                "chapter_id": "chapter-1",
                "index": 1,
                "title": "外婆家的童年",
                "intro": "记忆最早回到外婆家的院子。",
                "pull_quotes": [{"text": "我小时候常在外婆家的院子里晒太阳。"}],
                "content": chapter_content,
                "sections": [
                    {
                        "section_id": "section-1",
                        "index": 1,
                        "title": "院子里的日子",
                        "section_type": "life_fragment",
                        "content": chapter_content,
                        "char_count": len(chapter_content),
                        "source_count": 2,
                    }
                ],
                "source_refs": [{"utterance_id": "u1"}, {"utterance_id": "u2"}],
            }
        ],
        "back_matter": {
            "afterword": {"kind": "afterword", "title": "后记", "content": "这本书还可以继续补充。"},
            "interview_records": {"kind": "interview_records", "title": "讲述记录", "content": ""},
            "version_note": {"kind": "version_note", "title": "版本说明", "content": ""},
            "source_appendix": {"kind": "source_appendix", "title": "章节来源", "content": "第1章：2 条。"},
        },
        "render_settings": render_settings,
    }


def _text_command(page, text: str):
    return next(command for command in page.commands if command.text == text)


def test_manuscript_design_changes_chapter_and_body_pdf_layout():
    manuscript = _sample_manuscript("这是正文，留给家人阅读。")
    base = PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD]
    designs = {
        template: configured_pdf_layout(base, template=template)
        for template in ("classic_family", "photo_memory", "oral_archive")
    }
    rendered = {}
    for template, layout in designs.items():
        pages = _build_manuscript_pdf_pages(
            title="外婆回忆录",
            content="外婆回忆录",
            manuscript=manuscript,
            layout=layout,
        )
        chapter_page = next(page for page in pages if page.anchor == "chapter-1")
        title = _text_command(chapter_page, "外婆家的童年")
        body = next(
            command
            for page in pages
            for command in page.commands
            if command.text == "这是正文，留给家人阅读。"
        )
        rendered[template] = (title.font_size, body.font_size, body.x, body.y)

    assert len(set(rendered.values())) == 3
    assert rendered["photo_memory"][0] > rendered["classic_family"][0]
    assert rendered["oral_archive"][1] < rendered["classic_family"][1]

    custom = configured_pdf_layout(
        base,
        template="custom",
        custom_layout={"font_scale": "large", "line_spacing": "relaxed", "page_margin": "wide"},
    )
    assert custom.font_size > base.font_size
    assert custom.line_height > base.line_height
    assert custom.margin_x > base.margin_x
    assert custom.max_line_units < base.max_line_units

    print_base = PDF_LAYOUTS[MemoirBookOutputExportLayout.LARGE_PRINT]
    for template in (*designs, "custom"):
        print_layout = configured_pdf_layout(print_base, template=template)
        assert print_layout.mirror_body_pages is True
        assert print_layout.inner_margin_delta == print_base.inner_margin_delta


def test_body_font_and_spacing_overrides_keep_preset_chapter_design():
    text = "这是正文，留给家人阅读。"
    base = PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD]
    for template in ("heritage_classic", "warm_journal", "modern_minimal"):
        manuscript = _sample_manuscript(text, cover_style="dark_album")
        manuscript["profile"]["template"] = template
        original = configured_pdf_layout(base, template=template)
        for family, font in (("serif", PDF_SERIF_FONT_NAME), ("sans", PDF_SANS_FONT_NAME)):
            adjusted = configured_pdf_layout(
                base, template=template,
                custom_layout={"font_family": family, "font_scale": "large", "line_spacing": "relaxed", "page_margin": "wide"},
            )
            assert adjusted.manuscript_template == original.manuscript_template
            assert adjusted.font_size == original.font_size + 2
            assert adjusted.margin_x == original.margin_x + 12
            pages = _build_manuscript_pdf_pages(title="外婆回忆录", content="", manuscript=manuscript, layout=adjusted)
            chapter = next(page for page in pages if page.anchor == "chapter-1")
            assert _text_command(chapter, text).font_name == font
            assert any(image.path.name == f"{template}.png" for image in chapter.images)
            assert pages[0].background_color == PDF_COVER_STYLES["dark_album"].background
            assert _text_command(chapter, "院子里的日子").font_name == (
                PDF_SANS_FONT_NAME if template == "modern_minimal" else PDF_SERIF_BOLD_FONT_NAME
            )


def test_legacy_preset_ignores_unused_adjustments_but_custom_keeps_them():
    legacy = _sample_manuscript("旧正文。")
    legacy["table_of_contents"][0]["chapter_id"] = "chapter-1"
    legacy["back_matter"]["unconfirmed_facts"] = {"kind": "unconfirmed_facts", "title": "待确认", "content": ""}
    legacy["schema_version"] = 3
    legacy["profile"]["template"] = "warm_journal"
    legacy["render_settings"]["custom_layout"] = {"font_scale": "large", "line_spacing": "relaxed", "page_margin": "wide"}
    assert MemoirBookManuscript.model_validate(legacy).render_settings.custom_layout.font_scale == "standard"
    assert stored_manuscript_design(legacy)[1].font_scale == "standard"
    upgraded = editable_manuscript(title="外婆回忆录", content="", snapshot=[], stored=legacy)
    assert upgraded.schema_version == 5
    assert upgraded.render_settings.custom_layout.font_scale == "standard"
    legacy["profile"]["template"] = "custom"
    assert MemoirBookManuscript.model_validate(legacy).render_settings.custom_layout.font_scale == "large"
    assert stored_manuscript_design(legacy)[1].font_scale == "large"


def test_additional_manuscript_templates_render_distinct_inner_pages_and_preserve_cover():
    templates = ("heritage_classic", "warm_journal", "modern_minimal")
    assert set(templates).issubset(get_args(MemoirBookManuscriptTemplate))
    pdf_hashes = set()
    section_styles = set()
    for template in templates:
        assert MemoirBookManuscriptProfile(template=template, title="外婆回忆录").template == template
        assert stored_manuscript_design({"profile": {"template": template}})[0] == template
        manuscript = _sample_manuscript("这是正文，留给家人阅读。", cover_style="dark_album")
        manuscript["profile"]["template"] = template
        layout = configured_pdf_layout(
            PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD], template=template
        )
        pages = _build_manuscript_pdf_pages(
            title="外婆回忆录", content="外婆回忆录", manuscript=manuscript,
            layout=layout,
        )
        assert pages[0].background_color == PDF_COVER_STYLES["dark_album"].background
        assert pages[-1].background_color == PDF_COVER_STYLES["dark_album"].background
        chapter = next(page for page in pages if page.anchor == "chapter-1")
        title = _text_command(chapter, "外婆家的童年")
        section = _text_command(chapter, "院子里的日子")
        quote = _text_command(chapter, "“我小时候常在外婆家的院子里晒太阳。”")
        body = _text_command(chapter, "这是正文，留给家人阅读。")
        section_styles.add((section.font_name, section.color, section.x))
        assert quote.color is not None
        assert chapter.decorations
        assert any(image.path.name == f"{template}.png" for image in chapter.images)
        footer = _pdf_page_commands(chapter, page_number=pages.index(chapter) + 1, layout=layout)[-1]
        assert str(chapter.body_page_number) in footer.text
        if template == "heritage_classic":
            assert title.x > section.x
            assert footer.text.startswith("· ")
        elif template == "warm_journal":
            assert title.x == section.x - 11
            assert any(decoration.line_width == 2.2 for decoration in chapter.decorations)
        else:
            assert title.font_name == PDF_SANS_FONT_NAME
            assert section.font_name == PDF_SANS_FONT_NAME
            assert body.font_name == PDF_SANS_FONT_NAME
            assert any(command.text == "01" for command in chapter.commands)
            assert footer.text == "01"

        export = render_book_output_export(
            title="外婆回忆录", version=1, content="外婆回忆录",
            manuscript=manuscript, export_format=MemoirBookOutputExportFormat.PDF,
        )
        assert export.content.startswith(b"%PDF-")
        pdf_hashes.add(sha256(export.content).digest())

    assert len(section_styles) == len(templates)
    assert len(pdf_hashes) == len(templates)


def test_generated_cover_and_chapter_artwork_render_in_reading_and_print_pdfs():
    for template in ("heritage_classic", "warm_journal", "modern_minimal"):
        for export_layout in (
            MemoirBookOutputExportLayout.STANDARD,
            MemoirBookOutputExportLayout.LARGE_PRINT,
        ):
            manuscript = _sample_manuscript("这是正文，留给家人阅读。", cover_style=template)
            manuscript["profile"]["template"] = template
            manuscript["render_settings"]["back_cover_quote"] = "愿这些记忆被家人慢慢翻看。"
            layout = configured_pdf_layout(PDF_LAYOUTS[export_layout], template=template)
            pages = _build_manuscript_pdf_pages(
                title="外婆回忆录", content="外婆回忆录", manuscript=manuscript,
                layout=layout,
            )
            cover = pages[0]
            back_cover = pages[-1]
            chapter = next(page for page in pages if page.anchor == "chapter-1")
            assert cover.background_image_path is not None
            assert cover.background_image_path.name == f"{template}_cover.jpg"
            assert cover.background_image_path.is_file()
            assert back_cover.background_image_path is not None
            assert back_cover.background_image_path.name == f"{template}_back_cover.jpg"
            assert back_cover.background_image_path.is_file()
            assert _text_command(cover, "外婆回忆录").text == "外婆回忆录"
            ornament = next(image for image in chapter.images if image.path.name == f"{template}.png")
            assert ornament.path.is_file()
            assert ornament.y > layout.margin_bottom
            export = render_book_output_export(
                title="外婆回忆录", version=1, content="外婆回忆录",
                manuscript=manuscript, export_format=MemoirBookOutputExportFormat.PDF,
                layout=export_layout,
            )
            assert export.content.startswith(b"%PDF-")
            assert b"/Subtype /Image" in export.content


def test_generated_back_cover_keeps_long_quote_above_brand():
    quote = "回忆会留在心里，" * 13
    for template in ("heritage_classic", "warm_journal", "modern_minimal"):
        manuscript = _sample_manuscript("正文。", cover_style=template)
        manuscript["profile"]["template"] = template
        manuscript["render_settings"]["back_cover_quote"] = quote
        layout = configured_pdf_layout(
            PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD], template=template
        )
        back_cover = _build_manuscript_pdf_pages(
            title="外婆回忆录", content="外婆回忆录", manuscript=manuscript,
            layout=layout,
        )[-1]
        quote_lines = [command for command in back_cover.commands if "回忆" in command.text]
        assert "".join(command.text for command in quote_lines) == quote
        assert min(command.y for command in quote_lines) > max(
            image.y + image.height for image in back_cover.images
        ) + 12


def test_generated_back_cover_elides_quote_that_exceeds_available_height():
    quote = "外婆留下的回忆仍然陪伴着我们。" * 35
    for template in ("heritage_classic", "warm_journal", "modern_minimal"):
        manuscript = _sample_manuscript("正文。", cover_style=template)
        manuscript["profile"]["template"] = template
        manuscript["render_settings"]["back_cover_quote"] = quote
        layout = configured_pdf_layout(
            PDF_LAYOUTS[MemoirBookOutputExportLayout.LARGE_PRINT], template=template
        )
        back_cover = _build_manuscript_pdf_pages(
            title="外婆回忆录", content="外婆回忆录", manuscript=manuscript,
            layout=layout,
        )[-1]
        quote_lines = [command for command in back_cover.commands if "外婆" in command.text]
        assert quote_lines[-1].text.endswith("…")
        assert min(command.y for command in quote_lines) > max(
            image.y + image.height for image in back_cover.images
        ) + 12
        assert manuscript["render_settings"]["back_cover_quote"] == quote


def test_generated_cover_shortens_only_displayed_subtitle_when_title_is_long():
    title = "外婆家的童年与那段留在心里的夏日旧时光" * 4
    subtitle = "根据口述和家人的回忆整理的一段漫长故事" * 6
    for template in ("heritage_classic", "warm_journal", "modern_minimal"):
        manuscript = _sample_manuscript("正文。", cover_style=template)
        manuscript["profile"].update(title=title, subtitle=subtitle, template=template)
        layout = configured_pdf_layout(
            PDF_LAYOUTS[MemoirBookOutputExportLayout.LARGE_PRINT], template=template
        )
        cover = _build_manuscript_pdf_pages(
            title=title, content=title, manuscript=manuscript, layout=layout
        )[0]
        subtitle_lines = [
            command for command in cover.commands
            if command.font_size == layout.font_size + 2
        ]
        assert subtitle_lines
        assert subtitle_lines[-1].text.endswith("…")
        assert min(command.y for command in subtitle_lines) > layout.margin_bottom + 72 + 54
        assert manuscript["profile"]["subtitle"] == subtitle


def test_long_new_template_headings_stay_within_reading_and_print_margins():
    _register_pdf_font()
    long_title = "外婆家的童年与那段留在心里的夏日旧时光" * 4
    long_section = "院子里那些很久以后依然记得的日子" * 3
    for template in ("heritage_classic", "warm_journal", "modern_minimal"):
        for export_layout in (
            MemoirBookOutputExportLayout.STANDARD,
            MemoirBookOutputExportLayout.LARGE_PRINT,
        ):
            manuscript = _sample_manuscript("这是正文，留给家人阅读。", cover_style=template)
            manuscript["profile"]["template"] = template
            manuscript["profile"]["title"] = long_title
            manuscript["chapters"][0]["title"] = long_title
            manuscript["chapters"][0]["sections"][0]["title"] = long_section
            layout = configured_pdf_layout(PDF_LAYOUTS[export_layout], template=template)
            pages = _build_manuscript_pdf_pages(
                title=long_title, content=long_title,
                manuscript=manuscript, layout=layout,
            )
            chapter_pages = [page for page in pages if page.section_title == long_title]
            assert chapter_pages
            title_lines = [
                command for page in chapter_pages for command in page.commands
                if command.font_size == layout.title_font_size and command.text in long_title
            ]
            section_lines = [
                command for page in chapter_pages for command in page.commands
                if command.font_size == layout.font_size + 2 and command.text in long_section
            ]
            assert len(title_lines) > 1
            assert len(section_lines) > 1
            assert "".join(command.text for command in title_lines) == long_title
            assert "".join(command.text for command in section_lines) == long_section
            assert all(command in chapter_pages[0].commands for command in title_lines)
            cover_title_lines = [
                command for command in pages[0].commands
                if command.text in long_title and command.font_size >= 18
            ]
            assert "".join(command.text for command in cover_title_lines) == long_title
            credit = _text_command(pages[0], "家人整理")
            assert min(command.y for command in cover_title_lines) > credit.y + 45
            for command in cover_title_lines:
                font_name = command.font_name or PDF_SERIF_FONT_NAME
                text_width = pdfmetrics.stringWidth(command.text, font_name, command.font_size)
                text_width += command.char_spacing * max(0, len(command.text) - 1)
                assert command.x >= layout.margin_x
                assert command.x + text_width <= layout.page_width - layout.margin_x
            ornament = next(
                image for image in chapter_pages[0].images
                if image.path.name == f"{template}.png"
            )
            assert ornament.y + ornament.height < min(command.y for command in title_lines) - 8
            assert ornament.y > layout.margin_bottom
            for page_number, page in enumerate(pages, start=1):
                if page not in chapter_pages:
                    continue
                inset = (
                    layout.inner_margin_delta if page_number % 2 else -layout.inner_margin_delta
                ) if layout.mirror_body_pages else 0
                right_edge = layout.page_width - layout.margin_x + inset
                for command in page.commands:
                    if command not in title_lines and command not in section_lines:
                        continue
                    font_name = command.font_name or PDF_SERIF_FONT_NAME
                    width = pdfmetrics.stringWidth(command.text, font_name, command.font_size)
                    width += command.char_spacing * max(0, len(command.text) - 1)
                    assert command.x + width <= right_edge + 1


def test_pdf_cover_styles_are_selectable_across_schema_service_and_export():
    assert set(get_args(MemoirBookCoverStyle)) == EXPECTED_PDF_COVER_STYLES
    assert EXPECTED_PDF_COVER_STYLES.issubset(PDF_COVER_STYLES)
    assert {style: normalize_book_cover_style(style) for style in EXPECTED_PDF_COVER_STYLES} == {
        style: style for style in EXPECTED_PDF_COVER_STYLES
    }


def test_manuscript_pdf_pages_use_book_page_semantics():
    content = "我小时候常在外婆家的院子里晒太阳。" * 80
    pages = _build_manuscript_pdf_pages(
        title="外婆回忆录",
        content="外婆回忆录",
        manuscript=_sample_manuscript(content),
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )

    first_chapter_index = next(index for index, page in enumerate(pages) if page.anchor == "chapter-1")
    front_pages = pages[:first_chapter_index]
    assert len(front_pages) == 3
    assert all(not page.header_title for page in front_pages)
    assert all(page.body_page_number is None for page in front_pages)
    assert all(page.show_footer is False for page in front_pages)
    assert pages[0].background_color is not None
    assert pages[0].text_color is not None
    assert pages[0].decorations
    assert all(decoration.kind == "line" for decoration in pages[0].decorations)
    assert all(command.text != "口述：待补充" for command in pages[0].commands)
    assert any(command.text == "目录" for command in front_pages[-1].commands)
    assert all(page.section_title != "扉页" for page in pages)
    assert all(page.section_title != "附录" for page in pages)

    chapter_pages = [page for page in pages if page.section_title == "外婆家的童年"]
    assert chapter_pages[0].body_page_number == 1
    assert chapter_pages[0].header_title == ""
    assert chapter_pages[0].show_footer is True
    assert chapter_pages[0].anchor == "chapter-1"
    assert chapter_pages[0].outline_title == "第一章 外婆家的童年"
    assert any(command.text == "第一章" for command in chapter_pages[0].commands)
    assert any(command.text == "外婆家的童年" for command in chapter_pages[0].commands)

    assert chapter_pages[1].body_page_number == 2
    assert chapter_pages[1].header_title == "外婆回忆录"
    assert chapter_pages[1].show_footer is True
    assert all(
        "本章整理自" not in command.text
        for page in chapter_pages
        for command in page.commands
    )
    assert any(command.text == "由老余口述协助整理" for command in pages[-1].commands)
    assert len(pages[-1].images) == 1
    assert "-dark-" in pages[-1].images[0].path.name
    assert "memoir-brand-v2" in pages[-1].images[0].path.name


def test_chapter_inline_illustrations_render_after_their_source_section(monkeypatch):
    manuscript = _sample_manuscript("第一小节正文。")
    manuscript["chapters"][0]["sections"] = [
        {
            "section_id": "section-1",
            "index": 1,
            "title": "第一小节",
            "section_type": "life_fragment",
            "content": "第一小节正文。",
            "char_count": 7,
            "source_count": 1,
            "source_ref_ids": ["utterance-1"],
        },
        {
            "section_id": "section-2",
            "index": 2,
            "title": "第二小节",
            "section_type": "life_fragment",
            "content": "第二小节正文。",
            "char_count": 7,
            "source_count": 1,
            "source_ref_ids": ["utterance-2"],
        },
    ]
    manuscript["chapters"][0]["illustrations"] = [
        {
            "image_asset_id": "opening",
            "storage_path": "unused-opening.png",
            "placement": "chapter_opening",
        },
        {
            "image_asset_id": "inline-2",
            "storage_path": "unused-inline-2.png",
            "placement": "chapter_inline",
            "section_id": "section-2",
            "source_utterance_id": "utterance-2",
        },
        {
            "image_asset_id": "inline-1",
            "storage_path": "unused-inline-1.png",
            "placement": "chapter_inline",
            "section_id": "section-1",
            "source_utterance_id": "utterance-1",
        },
    ]

    monkeypatch.setattr(
        book_output_export,
        "_add_illustration",
        lambda builder, illustration: builder.add_text(
            f"IMAGE:{illustration['image_asset_id']}"
        ),
    )
    pages = _build_manuscript_pdf_pages(
        title="外婆回忆录",
        content="外婆回忆录",
        manuscript=manuscript,
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )
    rendered_text = [
        command.text
        for page in pages
        for command in page.commands
    ]

    assert rendered_text.index("IMAGE:opening") < rendered_text.index("第一小节")
    assert rendered_text.index("第一小节正文。") < rendered_text.index("IMAGE:inline-1")
    assert rendered_text.index("IMAGE:inline-1") < rendered_text.index("第二小节")
    assert rendered_text.index("第二小节正文。") < rendered_text.index("IMAGE:inline-2")


def test_book_illustrations_do_not_render_asset_metadata_as_visible_captions(
    monkeypatch,
    tmp_path,
):
    image_path = tmp_path / "illustration.png"
    Image.new("RGB", (1280, 720), "#8a7967").save(image_path)
    caption = "我上午去看了我爷爷，给他买了两盒西瓜。"
    disclosure = "AI 根据讲述内容生成的意象插图，不是真实照片。"
    illustration = {
        "image_asset_id": "asset-1",
        "storage_path": image_path.name,
        "placement": "chapter_inline",
        "section_id": "section-1",
        "caption": caption,
        "disclosure": disclosure,
    }
    manuscript = _sample_manuscript("这一段正文后面放一张插图。")
    manuscript["chapters"][0]["illustrations"] = [illustration]
    manuscript["image_appendix"] = [
        {
            **illustration,
            "image_asset_id": "asset-2",
            "placement": "appendix",
        }
    ]
    monkeypatch.setattr(book_output_export, "DATA_DIR", tmp_path)

    pages = _build_manuscript_pdf_pages(
        title="外婆回忆录",
        content="外婆回忆录",
        manuscript=manuscript,
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )
    rendered_text = [
        command.text
        for page in pages
        for command in page.commands
    ]

    assert caption not in rendered_text
    assert disclosure not in rendered_text
    assert sum(
        command.path == image_path
        for page in pages
        for command in page.images
    ) == 2


def test_moonlit_courtyard_cover_style_uses_system_backgrounds():
    manuscript = _sample_manuscript(
        "因为是家乡，所以一想起来成都，心里面就觉得很温暖、很踏实。",
        cover_style="moonlit_courtyard",
    )
    manuscript["profile"]["title"] = "回望这一生"
    manuscript["profile"]["subtitle"] = "余晋口述回忆录"
    manuscript["profile"]["narrator_name"] = "余晋"
    manuscript["render_settings"]["back_cover_quote"] = "因为是家乡，所以一想起来成都，心里面就觉得很温暖、很踏实。"

    pages = _build_manuscript_pdf_pages(
        title="回望这一生",
        content="回望这一生",
        manuscript=manuscript,
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )

    assert pages[0].background_image_path is not None
    assert pages[0].background_image_path.name == "moonlit_courtyard_cover.jpg"
    assert pages[-1].background_image_path is not None
    assert pages[-1].background_image_path.name == "moonlit_courtyard_back_cover.jpg"
    assert any(command.text == "家庭回忆录" for command in pages[0].commands)
    assert any(command.text == "回望这一生" for command in pages[0].commands)
    assert any(command.text == "由老余口述协助整理" for command in pages[-1].commands)
    assert len(pages[-1].images) == 1
    assert "-dark-" in pages[-1].images[0].path.name

    export = render_book_output_export(
        title="回望这一生",
        version=1,
        content="回望这一生",
        manuscript=manuscript,
        export_format=MemoirBookOutputExportFormat.PDF,
    )
    assert export.media_type == "application/pdf"
    assert isinstance(export.content, bytes)
    assert export.content.startswith(b"%PDF-")
    assert b"/Subtype /Image" in export.content


def test_navy_gilt_cover_style_uses_system_backgrounds():
    manuscript = _sample_manuscript(
        "因为是家乡，所以一想起来成都，心里面就觉得很温暖、很踏实。",
        cover_style="navy_gilt",
    )
    manuscript["profile"]["title"] = "回望这一生"
    manuscript["profile"]["subtitle"] = "余晋口述回忆录"
    manuscript["profile"]["narrator_name"] = "余晋"
    manuscript["render_settings"]["back_cover_quote"] = "因为是家乡，所以一想起来成都，心里面就觉得很温暖、很踏实。"

    pages = _build_manuscript_pdf_pages(
        title="回望这一生",
        content="回望这一生",
        manuscript=manuscript,
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )

    assert pages[0].background_image_path is not None
    assert pages[0].background_image_path.name == "navy_gilt_cover.jpg"
    assert pages[-1].background_image_path is not None
    assert pages[-1].background_image_path.name == "navy_gilt_back_cover.jpg"
    assert any(command.text == "家庭回忆录" for command in pages[0].commands)
    assert any(command.text == "回望这一生" for command in pages[0].commands)
    assert any(command.text == "由老余口述协助整理" for command in pages[-1].commands)
    assert len(pages[-1].images) == 1
    assert "-light-" in pages[-1].images[0].path.name

    export = render_book_output_export(
        title="回望这一生",
        version=1,
        content="回望这一生",
        manuscript=manuscript,
        export_format=MemoirBookOutputExportFormat.PDF,
    )
    assert export.media_type == "application/pdf"
    assert isinstance(export.content, bytes)
    assert export.content.startswith(b"%PDF-")
    assert b"/Subtype /Image" in export.content


def test_image_cover_styles_use_distinct_title_typography():
    def build_pages(cover_style: str):
        manuscript = _sample_manuscript(
            "因为是家乡，所以一想起来成都，心里面就觉得很温暖、很踏实。",
            cover_style=cover_style,
        )
        manuscript["profile"]["title"] = "回望这一生"
        manuscript["profile"]["subtitle"] = "余晋口述回忆录"
        manuscript["render_settings"]["back_cover_quote"] = (
            "因为是家乡，所以一想起来成都，心里面就觉得很温暖、很踏实。"
        )
        return _build_manuscript_pdf_pages(
            title="回望这一生",
            content="回望这一生",
            manuscript=manuscript,
            layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
            show_source_appendix=None,
        )

    watercolor_pages = build_pages("moonlit_courtyard")
    navy_pages = build_pages("navy_gilt")

    watercolor_title = _text_command(watercolor_pages[0], "回望这一生")
    navy_title = _text_command(navy_pages[0], "回望这一生")
    watercolor_subtitle = _text_command(watercolor_pages[0], "余晋口述回忆录")
    navy_subtitle = _text_command(navy_pages[0], "余晋口述回忆录")
    watercolor_quote = next(command for command in watercolor_pages[-1].commands if "因为是家乡" in command.text)
    navy_quote = next(command for command in navy_pages[-1].commands if "因为是家乡" in command.text)

    assert watercolor_title.font_name == PDF_SERIF_FONT_NAME
    assert navy_title.font_name == PDF_SERIF_BOLD_FONT_NAME
    assert watercolor_title.font_size != navy_title.font_size
    assert watercolor_title.char_spacing != navy_title.char_spacing
    assert watercolor_subtitle.font_name == PDF_SERIF_FONT_NAME
    assert navy_subtitle.font_name == PDF_SERIF_BOLD_FONT_NAME
    assert watercolor_subtitle.font_size != navy_subtitle.font_size
    assert watercolor_subtitle.char_spacing != navy_subtitle.char_spacing
    assert watercolor_quote.font_name == PDF_SERIF_FONT_NAME
    assert navy_quote.font_name == PDF_SERIF_BOLD_FONT_NAME
    assert watercolor_quote.font_size != navy_quote.font_size
    assert watercolor_quote.char_spacing != navy_quote.char_spacing


def test_table_of_contents_links_to_numbered_chapter_pages():
    pages = _build_manuscript_pdf_pages(
        title="外婆回忆录",
        content="外婆回忆录",
        manuscript=_sample_manuscript("我小时候常在外婆家的院子里晒太阳。"),
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )

    toc_page = next(page for page in pages if page.section_title == "目录")
    toc_command = next(command for command in toc_page.commands if "外婆家的童年" in command.text)
    page_command = next(command for command in toc_page.commands if command.text == "1")

    assert toc_command.target_anchor == "chapter-1"
    assert toc_command.text == "第一章　外婆家的童年"
    assert toc_command.font_name == PDF_SERIF_BOLD_FONT_NAME
    assert page_command.target_anchor == "chapter-1"
    assert page_command.x > toc_command.x
    assert page_command.y == toc_command.y
    assert all("院子里的日子" not in command.text for command in toc_page.commands)

    chapter_page = next(page for page in pages if page.anchor == "chapter-1")
    assert any(
        command.text == "院子里的日子"
        and command.font_name == PDF_SERIF_BOLD_FONT_NAME
        for command in chapter_page.commands
    )


def test_table_of_contents_skips_links_without_chapter_pages():
    manuscript = _sample_manuscript("我小时候常在外婆家的院子里晒太阳。")
    manuscript["table_of_contents"].append({"index": 2, "title": "还没有写完的一章"})

    pages = _build_manuscript_pdf_pages(
        title="外婆回忆录",
        content="外婆回忆录",
        manuscript=manuscript,
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )

    toc_page = next(page for page in pages if page.section_title == "目录")
    missing_command = next(command for command in toc_page.commands if "还没有写完" in command.text)

    assert missing_command.target_anchor is None


@pytest.mark.parametrize("prepared_by,credit", [("", "家人整理"), ("家人整理", "家人整理"), ("小李", "整理：小李")])
def test_cover_credit_avoids_repeated_default_role(prepared_by, credit):
    for style in PDF_COVER_STYLES:
        manuscript = _sample_manuscript("院子里有一棵树。", cover_style=style)
        manuscript["profile"]["prepared_by"] = prepared_by
        pages = _build_manuscript_pdf_pages(
            title="外婆回忆录", content="", manuscript=manuscript,
            layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        )
        cover_text = "\n".join(command.text for command in pages[0].commands)
        assert credit in cover_text
        assert "整理：家人整理" not in cover_text


def test_export_filename_preserves_natural_memoir_titles():
    family_export = render_book_output_export(
        title="家庭回忆录",
        version=1,
        content="正文",
        manuscript={},
        export_format=MemoirBookOutputExportFormat.TXT,
    )
    oral_export = render_book_output_export(
        title="外婆口述回忆录",
        version=1,
        content="正文",
        manuscript={},
        export_format=MemoirBookOutputExportFormat.TXT,
    )

    assert family_export.filename == "家庭回忆录.txt"
    assert oral_export.filename == "《外婆口述回忆录》.txt"
    assert "口述的回忆录" not in oral_export.filename


def test_pdf_line_wrapping_respects_chinese_forbidden_breaks():
    lines = _wrap_pdf_line(
        "小时候，院子里有桂花树。后来，我们常常说：“那里像家。”",
        max_units=7,
    )

    assert len(lines) > 1
    assert all(line[0] not in LINE_START_FORBIDDEN_CHARS for line in lines[1:])
    assert all(line[-1] not in LINE_END_FORBIDDEN_CHARS for line in lines[:-1])


def test_pdf_line_wrapping_keeps_common_words_and_number_units_together():
    lines = _wrap_pdf_line(
        "妈妈会把那些熟透的葡萄拿出来，因为这件事情已经过去 97 岁那年很久。",
        max_units=8,
    )
    joined_breaks = "\n".join(lines)

    assert COMMON_CHINESE_UNBREAKABLE_WORDS
    assert "那\n些" not in joined_breaks
    assert "事\n情" not in joined_breaks
    assert "因\n为" not in joined_breaks
    assert "97 \n岁" not in joined_breaks
    assert "97\n岁" not in joined_breaks


def test_manuscript_pdf_export_preserves_nonempty_editorial_copy_and_user_title():
    manuscript = _sample_manuscript("夏天的晚上，我就坐在院子里边看月亮。那个院子里有一棵葡萄树。")
    manuscript["profile"]["title"] = "我的小时候"
    manuscript["profile"]["subtitle"] = "回望这一生"
    manuscript["profile"]["narrator_name"] = "余晋"
    manuscript["front_matter"][2]["content"] = "这本书记录的是「爷爷主题回忆录」中的生活。"
    manuscript["chapters"].append(
        {
            "chapter_id": "chapter-2",
            "index": 2,
            "title": "职场往事",
            "intro": "从口述内容看，这一章写工作。",
            "pull_quotes": [],
            "content": "工作占据了人生中很长的一段时间。",
            "source_refs": [],
        }
    )
    manuscript["render_settings"]["back_cover_quote"] = "凡是过往皆是序章"

    pages = _build_manuscript_pdf_pages(
        title="我的小时候",
        content="我的小时候",
        manuscript=manuscript,
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )

    all_text = "\n".join(command.text for page in pages for command in page.commands)
    assert "回望这一生" in all_text
    assert "我的小时候" in all_text
    assert "爷爷主题回忆录" in all_text
    assert "凡是过往皆是序章" in all_text

    export = render_book_output_export(
        title="我的小时候",
        version=1,
        content="我的小时候",
        manuscript=manuscript,
        export_format=MemoirBookOutputExportFormat.PDF,
    )
    assert export.filename == "《我的小时候》_阅读版.pdf"


def test_chapter_epigraph_does_not_repeat_as_first_body_sentence():
    manuscript = _sample_manuscript("我小时候常在外婆家的院子里晒太阳。后来我常听外婆讲老屋的事。")

    pages = _build_manuscript_pdf_pages(
        title="外婆回忆录",
        content="外婆回忆录",
        manuscript=manuscript,
        layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD],
        show_source_appendix=None,
    )
    chapter_pages = [page for page in pages if page.section_title == "外婆家的童年"]
    chapter_text = [command.text for page in chapter_pages for command in page.commands]

    assert chapter_text.count("“我小时候常在外婆家的院子里晒太阳。”") == 1
    assert any(text.startswith("后来我常听外婆讲老屋的事") for text in chapter_text)
    body_repeats = [text for text in chapter_text if text == "　　我小时候常在外婆家的院子里晒太阳。"]
    assert body_repeats == []


def test_body_text_indents_paragraphs_and_avoids_orphan_starts():
    layout = PDFLayout(
        page_width=420,
        page_height=160,
        font_size=12,
        line_height=20,
        margin_x=42,
        margin_top=40,
        margin_bottom=40,
        title_font_size=20,
        heading_font_size=16,
        small_font_size=10,
        max_line_units=8,
    )
    builder = PDFBookBuilder(title="外婆回忆录", layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.add_text("占位")
    builder.add_text("占位")
    builder.add_text("占位")

    builder.add_body_text("第一段需要分成两行。\n第二段也要缩进。")

    assert len(builder.pages) == 3
    assert builder.pages[1].body_page_number == 2
    first_body_line = builder.pages[1].commands[0]
    assert first_body_line.text.startswith("第一段")
    assert first_body_line.x == layout.margin_x + layout.font_size * 2

    second_paragraph = next(
        command
        for page in builder.pages[1:]
        for command in page.commands
        if command.text.startswith("第二段")
    )
    assert second_paragraph.x == layout.margin_x + layout.font_size * 2
    assert all(not command.text.startswith("第一段") for command in builder.pages[0].commands)


@pytest.mark.parametrize("template", ["heritage_classic", "warm_journal", "modern_minimal"])
def test_section_heading_keeps_body_start_on_the_same_page(template):
    layout = configured_pdf_layout(
        PDF_LAYOUTS[MemoirBookOutputExportLayout.LARGE_PRINT], template=template
    )
    builder = PDFBookBuilder(title="分页回归", layout=layout)
    builder.new_page("测试章节", body_numbering=True)
    builder.add_text("前一段正文")
    builder.y = layout.margin_bottom + 100
    heading = "童年院落里的微光与后来漫长岁月回忆"
    body = "这一段讲述只用于分页测试，标题之后至少保留两行正文，阅读时不必翻页寻找开头。"

    builder.add_section_heading(heading)
    builder.add_body_text(body)

    heading_page = next(
        page for page in builder.pages if any(command.text.startswith("童年院落") for command in page.commands)
    )
    assert any(command.text.startswith("这一段讲述") for command in heading_page.commands)
    assert len([command for command in heading_page.commands if command.font_size == layout.font_size]) >= 2
    assert not any(command.text.startswith("童年院落") for command in builder.pages[0].commands)


def test_pdf_heading_and_toc_do_not_leave_a_single_character_last_line():
    layout = configured_pdf_layout(
        PDF_LAYOUTS[MemoirBookOutputExportLayout.LARGE_PRINT], template="warm_journal"
    )
    builder = PDFBookBuilder(title="换行回归", layout=layout)
    builder.new_page("正文", body_numbering=True)
    heading = "童年院落里的微光与后来漫长的岁月里"
    builder.add_section_heading(heading)
    heading_lines = [command.text for command in builder.current_page.commands]

    assert len(heading_lines) == 2
    assert "".join(heading_lines) == heading
    assert len(heading_lines[-1]) > 1

    builder.new_page("目录", show_footer=False)
    label = f"1.1　{heading}"
    builder.add_toc_row(label=label, page_text="", indent=18, font_size=14)
    toc_lines = [command.text for command in builder.current_page.commands]

    assert "".join(toc_lines) == label
    assert len(toc_lines[-1]) > 1


def test_pdf_text_and_toc_reserve_their_actual_line_height():
    layout = PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD]
    builder = PDFBookBuilder(title="行高回归", layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.add_text("前文")
    builder.y = layout.margin_bottom + layout.line_height + 1

    builder.add_text("大行高标题", line_height=layout.line_height + 12)

    assert all(command.text != "大行高标题" for command in builder.pages[0].commands)
    builder.y = layout.margin_bottom + layout.line_height + 1
    builder.add_toc_row(label="目录测试", page_text="2")
    assert all(command.text != "目录测试" for command in builder.pages[1].commands)


def test_print_chapters_keep_right_page_alignment_and_mirrored_body_margins():
    paragraph = "这一段虚构讲述只用于测试章节分页和装订边距。"
    content = "\n".join([paragraph] * 8)
    manuscript = _sample_manuscript(content)
    second_chapter = {
        **manuscript["chapters"][0],
        "chapter_id": "chapter-2",
        "index": 2,
        "title": "第二章的测试故事",
        "content": paragraph,
        "sections": [{**manuscript["chapters"][0]["sections"][0], "content": paragraph}],
    }
    manuscript["chapters"].append(second_chapter)
    manuscript["table_of_contents"].append({"index": 2, "title": second_chapter["title"]})
    manuscript["chapters"].append({**second_chapter, "chapter_id": "chapter-3", "index": 3})
    manuscript["table_of_contents"].append({"index": 3, "title": second_chapter["title"]})
    layout = configured_pdf_layout(
        PDF_LAYOUTS[MemoirBookOutputExportLayout.LARGE_PRINT], template="warm_journal"
    )

    pages = _build_manuscript_pdf_pages(
        title="分页测试", content="", manuscript=manuscript, layout=layout
    )

    assert all((index + 1) % 2 == 1 for index, page in enumerate(pages) if page.anchor)
    blank_pages = [page for page in pages if not page.commands and not page.images and not page.decorations]
    assert blank_pages
    assert all(not page.show_footer and not page.header_title and page.body_page_number is None for page in blank_pages)
    for index, page in enumerate(pages):
        body_start = next((command for command in page.commands if command.text.startswith("这一段虚构")), None)
        if body_start is None:
            continue
        assert body_start.x == (
            layout.margin_x
            + (layout.inner_margin_delta if (index + 1) % 2 else -layout.inner_margin_delta)
            + 2 * layout.font_size
        )


def test_export_cache_coalesces_processes_and_invalidates_owner_or_renderer_changes(tmp_path, monkeypatch):
    import multiprocessing
    import time
    from dataclasses import replace
    from app.services.file_exports import ExportFile
    from app.services.memoir import book_output_export_cache as cache
    from app.services.memoir.book_output_export_cache import PreparedBookExport

    monkeypatch.setattr(cache.content_storage, "PROJECTS_DIR", tmp_path / "projects")
    prepared = PreparedBookExport(
        owner_id="owner", project_id="project", version_id="version", version=1,
        title="写给他的话", content="正文", manuscript=None,
        export_format=MemoirBookOutputExportFormat.PDF, layout=MemoirBookOutputExportLayout.STANDARD,
        show_source_appendix=False, render_date="2026-10-06",
    )
    log = tmp_path / "renders.txt"

    def render(**_kwargs):
        with log.open("a") as file:
            file.write("render\n")
        time.sleep(0.05)
        return ExportFile(prepared.filename, "application/pdf", b"%PDF-test\n%%EOF\n")

    def worker():
        result = cache.render_cached_book_export(prepared, prepared.cache_key, render)
        assert result.content.startswith(b"%PDF-")

    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=worker) for _ in range(3)]
    for process in workers:
        process.start()
    for process in workers:
        process.join(timeout=5)
        assert process.exitcode == 0
    assert log.read_text() == "render\n"
    cache.render_cached_book_export(prepared, prepared.cache_key, render)
    assert log.read_text() == "render\n"
    assert replace(prepared, owner_id="other-owner").cache_key != prepared.cache_key
    assert replace(prepared, render_date="2026-10-07").cache_key != prepared.cache_key
    monkeypatch.setattr(cache, "BOOK_EXPORT_RENDER_REVISION", cache.BOOK_EXPORT_RENDER_REVISION + 1)
    upgraded = prepared.cache_key  # The already-prepared request remains fixed.
    assert replace(prepared).cache_key != upgraded


def test_illustration_file_content_changes_export_signature(tmp_path, monkeypatch):
    from dataclasses import replace
    from app.schemas.memoir_book import MemoirBookManuscript
    from app.services.memoir.book_output_export_cache import PreparedBookExport

    monkeypatch.setattr(book_output_export, "DATA_DIR", tmp_path)
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"first image")
    manuscript = _sample_manuscript("正文")
    manuscript["back_matter"]["unconfirmed_facts"] = {"kind": "unconfirmed_facts", "title": "待核实"}
    manuscript["table_of_contents"][0]["chapter_id"] = "chapter-1"
    manuscript["chapters"][0]["illustrations"] = [{"image_asset_id": "image-1", "storage_path": "photo.png"}]
    prepared = PreparedBookExport(
        owner_id="owner", project_id="project", version_id="version", version=1,
        title="写给他的话", content="正文", manuscript=MemoirBookManuscript.model_validate(manuscript),
        export_format=MemoirBookOutputExportFormat.PDF, layout=MemoirBookOutputExportLayout.STANDARD,
        show_source_appendix=False, render_date="2026-10-06",
    )
    original = prepared.cache_key
    photo.write_bytes(b"changed image")
    assert replace(prepared).cache_key != original
    photo.unlink()
    assert replace(prepared).cache_key != original


@pytest.mark.parametrize("display_size", ["cover_frame", "full_width", "cover_full_bleed"])
def test_uploaded_cover_photo_never_sits_under_cover_text(tmp_path, monkeypatch, display_size):
    monkeypatch.setattr(book_output_export, "DATA_DIR", tmp_path)
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (480, 640), "red").save(photo)
    long_title = "风雨六十年——一个老码头工人的口述人生记忆"
    designs = [(style, style if style in {"heritage_classic", "warm_journal", "modern_minimal"} else "classic_family") for style in PDF_COVER_STYLES]
    designs.append(("warm_journal", "custom"))
    for (cover_style, template), title in [(design, title) for design in designs for title in ("外婆回忆录", long_title)]:
        manuscript = _sample_manuscript("正文保持原样。", cover_style=cover_style)
        manuscript["profile"].update(title=title, template=template, narrator_name="外婆")
        layout = configured_pdf_layout(PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD], template=template)
        original = _build_manuscript_pdf_pages(title=title, content="", manuscript=manuscript, layout=layout)
        manuscript["render_settings"]["cover_image"] = {
            "image_asset_id": "photo", "storage_path": "photo.jpg", "display_size": display_size,
        }
        pages = _build_manuscript_pdf_pages(title=title, content="", manuscript=manuscript, layout=layout)
        cover = pages[0]
        texts = [command.text for command in cover.commands]
        assert title.replace("——", "") in "".join(texts).replace("——", "")
        assert "根据口述访谈整理" in texts
        assert any("口述：外婆" in text for text in texts)
        assert any("家人整理" in text for text in texts)
        if display_size == "cover_full_bleed":
            assert cover.background_image_path == photo
            assert cover.background_image_crop_to_fill is True
            shade_top = cover.background_image_bottom_shade * layout.page_height
            assert all(command.y + command.font_size <= shade_top for command in cover.commands)
        else:
            assert cover.background_image_path == original[0].background_image_path
            [image] = cover.images
            assert image.path == photo
            assert image.height >= 120
            assert abs(image.width / image.height - 480 / 640) < 0.01
            assert all(
                command.y >= image.y + image.height or command.y + command.font_size <= image.y
                for command in cover.commands
            )
        assert pages[1:] == original[1:]


def test_pdf_image_crop_to_fill_matches_box_and_shades_bottom(tmp_path):
    image_path = tmp_path / "landscape.jpg"
    Image.new("RGB", (1600, 1200), (240, 240, 240)).save(image_path)

    source = _optimized_pdf_image_source(PDFImageCommand(
        path=image_path, x=0, y=0, width=420, height=595, crop_to_fill=True, bottom_shade=0.5,
    ))

    width, height = source.getSize()
    assert abs(width / height - 420 / 595) < 0.01
    pixels = source.getRGBData()
    top, bottom = pixels[0], pixels[-3]
    assert top > 200 and bottom < 80
