from app.services.memoir.book_output_export import (
    LINE_END_FORBIDDEN_CHARS,
    LINE_START_FORBIDDEN_CHARS,
    PDF_LAYOUTS,
    PDFBookBuilder,
    PDFLayout,
    MemoirBookOutputExportLayout,
    _build_manuscript_pdf_pages,
    _wrap_pdf_line,
)


def _sample_manuscript(chapter_content: str) -> dict:
    return {
        "profile": {
            "title": "外婆回忆录",
            "subtitle": "根据口述访谈整理",
            "prepared_by": "家人整理",
        },
        "front_matter": [
            {"kind": "cover", "title": "封面", "content": ""},
            {"kind": "title_page", "title": "扉页", "content": "家人内部保存。"},
            {"kind": "preface", "title": "前言", "content": "这些故事从家人的讲述开始。"},
        ],
        "table_of_contents": [{"index": 1, "title": "外婆家的童年", "source_count": 2}],
        "chapters": [
            {
                "chapter_id": "chapter-1",
                "index": 1,
                "title": "外婆家的童年",
                "intro": "记忆最早回到外婆家的院子。",
                "pull_quotes": [{"text": "我小时候常在外婆家的院子里晒太阳。"}],
                "content": chapter_content,
                "source_refs": [{"utterance_id": "u1"}, {"utterance_id": "u2"}],
            }
        ],
        "back_matter": {
            "afterword": {"kind": "afterword", "title": "后记", "content": "这本书还可以继续补充。"},
            "interview_records": {"kind": "interview_records", "title": "讲述记录", "content": ""},
            "version_note": {"kind": "version_note", "title": "版本说明", "content": ""},
            "source_appendix": {"kind": "source_appendix", "title": "章节来源", "content": "第1章：2 条。"},
        },
        "render_settings": {"show_source_appendix": True},
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

    chapter_pages = [page for page in pages if page.section_title == "外婆家的童年"]
    assert chapter_pages[0].body_page_number == 1
    assert chapter_pages[0].header_title == ""
    assert chapter_pages[0].show_footer is False
    assert any(command.text == "第一章" for command in chapter_pages[0].commands)
    assert any(command.text == "外婆家的童年" for command in chapter_pages[0].commands)

    assert chapter_pages[1].body_page_number == 2
    assert chapter_pages[1].header_title == "外婆回忆录"
    assert chapter_pages[1].show_footer is True


def test_pdf_line_wrapping_respects_chinese_forbidden_breaks():
    lines = _wrap_pdf_line(
        "小时候，院子里有桂花树。后来，我们常常说：“那里像家。”",
        max_units=7,
    )

    assert len(lines) > 1
    assert all(line[0] not in LINE_START_FORBIDDEN_CHARS for line in lines[1:])
    assert all(line[-1] not in LINE_END_FORBIDDEN_CHARS for line in lines[:-1])


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
