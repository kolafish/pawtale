from app.services.memoir.book_output_export import (
    PDF_LAYOUTS,
    MemoirBookOutputExportLayout,
    _build_manuscript_pdf_pages,
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
