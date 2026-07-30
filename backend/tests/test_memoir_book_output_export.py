from typing import get_args

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.ttfonts import TTFont

from app.schemas.memoir_book import MemoirBookCoverStyle
from app.services.memoir import book_output_export
from app.services.memoir.book_outputs import _cover_style as normalize_book_cover_style
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
    PDF_SANS_FONT_PATH,
    PDF_SERIF_BOLD_FONT_PATH,
    PDF_SERIF_BOLD_FONT_NAME,
    PDF_SERIF_FONT_NAME,
    PDFBookBuilder,
    PDFImageCommand,
    PDFLayout,
    MemoirBookOutputExportLayout,
    _optimized_pdf_image_source,
    _wrap_pdf_line,
)
from app.services.memoir.book_output_snapshots import (
    reading_content,
    reading_paragraphs,
)
EXPECTED_PDF_COVER_STYLES = {
    "classic_plain",
    "warm_gray",
    "dark_album",
    "moonlit_courtyard",
    "navy_gilt",
}


def test_reading_layout_preserves_text_while_splitting_long_legacy_blocks():
    content = "".join(
        [
            "十九年前，我们因为一次偶然的点赞认识。",
            "后来虽然不常见面，但每次联系都觉得很亲切。",
            "那些年各自忙着生活，偶尔会在朋友圈里知道对方的近况。",
            "直到这个春天，我们终于约着一起去西山看山桃花。",
            "一路上聊起从前，才发现很多记忆并没有因为时间过去而消失。",
            "回到家以后，我仍然记得山路上的风和大家说笑的声音。",
        ]
    )

    paragraphs = reading_paragraphs(content)

    assert len(paragraphs) > 1
    assert "".join(paragraphs) == content
    assert reading_content("第一段。\n\n第二段。") == "第一段。\n第二段。"


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

    front_pages = pages[:4]
    assert all(not page.header_title for page in front_pages)
    assert all(page.body_page_number is None for page in front_pages)
    assert all(page.show_footer is False for page in front_pages)
    assert pages[0].background_color is not None
    assert pages[0].text_color is not None
    assert pages[0].decorations
    assert all(decoration.kind == "line" for decoration in pages[0].decorations)
    assert all(command.text != "口述：待补充" for command in pages[0].commands)
    assert sum(
        command.text == "根据口述访谈整理"
        for command in front_pages[1].commands
    ) == 1

    chapter_pages = [page for page in pages if page.section_title == "外婆家的童年"]
    assert chapter_pages[0].body_page_number == 1
    assert chapter_pages[0].header_title == ""
    assert chapter_pages[0].show_footer is True
    assert chapter_pages[0].anchor == "chapter-1"
    assert chapter_pages[0].outline_title == "第一章 外婆家的童年"
    assert any(command.text == "第一章" for command in chapter_pages[0].commands)
    assert any(command.text == "外婆家的童年" for command in chapter_pages[0].commands)

    assert chapter_pages[1].body_page_number == 2
    assert chapter_pages[1].header_title == "外婆的回忆录"
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
    watercolor_quote = _text_command(watercolor_pages[-1], "因为是家乡，")
    navy_quote = _text_command(navy_pages[-1], "因为是家乡，")

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
    section_command = next(
        command
        for command in toc_page.commands
        if "院子里的日子" in command.text
    )
    assert section_command.text == "1.1　院子里的日子"
    assert section_command.target_anchor == "chapter-1"

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

    assert family_export.filename.startswith("家庭回忆录_")
    assert oral_export.filename.startswith("《外婆口述回忆录》_")
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


def test_manuscript_pdf_export_sanitizes_legacy_copy_and_cover_identity():
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
    assert "余晋口述回忆录" in all_text
    assert "爷爷主题回忆录" not in all_text
    assert "从口述内容看" not in all_text
    assert "凡是过往皆是序章" not in all_text

    export = render_book_output_export(
        title="我的小时候",
        version=1,
        content="我的小时候",
        manuscript=manuscript,
        export_format=MemoirBookOutputExportFormat.PDF,
    )
    assert export.filename.startswith("《回望这一生》_阅读版_")


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
