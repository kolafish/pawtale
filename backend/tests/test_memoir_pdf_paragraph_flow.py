"""Regressions from a short synthetic memoir exported during launch acceptance."""

import pytest

from app.services.memoir.book_output_export import _add_chapters
from app.services.memoir.book_output_pdf_renderer import (
    PDF_LAYOUTS,
    PDFBookBuilder,
    MemoirBookOutputExportLayout,
    configured_pdf_layout,
)


@pytest.mark.parametrize("edition", list(MemoirBookOutputExportLayout))
@pytest.mark.parametrize("separator", ["\n\n", "\r\n\r\n", "\n \n\n"])
def test_paragraph_separators_do_not_add_an_extra_gap_or_page(edition, separator):
    layout = PDF_LAYOUTS[edition]
    paragraphs = ["这一段讲述保留原有字号和段首缩进。"] * 18

    def render(joiner):
        builder = PDFBookBuilder(title="模拟回忆录", layout=layout)
        builder.new_page("正文", body_numbering=True)
        builder.add_body_text(joiner.join(paragraphs))
        return [[(line.text, line.x, line.y, line.font_size) for line in page.commands] for page in builder.pages]

    assert render(separator) == render("\n")


def test_reading_short_chapter_keeps_its_last_lines_with_the_story():
    opening = (
        "1978年，我开始在县里的修理厂学修自行车。师傅姓陈，大家都叫他老陈。"
        "第一天，他让我先学补胎。我把内胎泡在水盆里，看见冒泡的地方，就知道哪里漏气了。"
    )
    ending = (
        "头一个月，我每天骑车半个小时去上班。那时候工资不高，一个月28块钱，"
        "我会拿20块钱交给母亲。后来我慢慢学会了调车闸和修车轮。最让我记得的是"
        "第一次独立修好邻居的自行车，邻居骑出去转了一圈，回来笑着说，这下放心了。"
    )
    layout = configured_pdf_layout(
        PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD], template="heritage_classic",
    )
    builder = PDFBookBuilder(title="模拟修车往事", layout=layout)
    _add_chapters(builder, [{
        "title": "进厂学艺", "pull_quotes": [{"text": opening}],
        "sections": [{"title": "学修车的第一年", "content": opening + "\n\n" + ending}],
    }])

    assert len(builder.pages) == 1
    page = builder.pages[0]
    text = "".join(line.text for line in page.commands)
    assert opening in text and ending in text
    body_lines = [line for line in page.commands if line.font_size == layout.font_size]
    assert body_lines and all(line.y >= layout.margin_bottom for line in body_lines)
    assert page.anchor == "chapter-1" and page.body_page_number == 1
