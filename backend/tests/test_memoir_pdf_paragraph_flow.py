"""Regressions from a short synthetic memoir exported during launch acceptance."""

import pytest
from PIL import Image

from app.services.memoir.book_output_export import _add_chapters
from app.services.memoir.book_output_pdf_renderer import (
    PDF_LAYOUTS,
    PDFBookBuilder,
    MemoirBookOutputExportLayout,
    configured_pdf_layout,
    _estimated_text_width,
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


@pytest.mark.parametrize("edition", list(MemoirBookOutputExportLayout))
def test_portrait_wraps_on_right_then_restores_full_width_without_losing_text(tmp_path, edition):
    path = tmp_path / "portrait.jpg"
    Image.new("RGB", (600, 900), "#789081").save(path)
    layout = PDF_LAYOUTS[edition]
    builder = PDFBookBuilder(title="往事", layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.y = layout.margin_bottom + layout.line_height * 2
    builder.add_image(path, wrap_right=True)
    text = "那年我们沿着河边去学校，走过田野和村庄，路上总有说不完的话。" * 18
    builder.add_body_text(text)
    pages = [page for page in builder.pages if page.images]
    assert len(pages) == 1
    page = pages[0]
    image = page.images[0]
    assert image.width / (layout.page_width - 2 * layout.margin_x) == pytest.approx(0.38)
    assert image.width / image.height == pytest.approx(600 / 900)
    beside = [line for line in page.commands if line.y + line.font_size > image.y]
    below = [line for line in page.commands if line.y + line.font_size < image.y - 8]
    assert beside and below
    assert all(line.x + _estimated_text_width(line.text, line.font_size) < image.x for line in beside)
    assert any(line.x + _estimated_text_width(line.text, line.font_size) > image.x for line in below)
    assert "".join(line.text for item in builder.pages for line in item.commands) == text
    assert all(line.y >= layout.margin_bottom for item in builder.pages for line in item.commands)
    builder.add_section_heading("下一个故事")
    assert builder.wrapped_image is None


@pytest.mark.parametrize("edition", list(MemoirBookOutputExportLayout))
@pytest.mark.parametrize("image_height", [900, 2400])
@pytest.mark.parametrize("long_heading", [False, True])
def test_section_heading_stays_with_its_first_portrait(tmp_path, monkeypatch, edition, image_height, long_heading):
    from app.services.memoir import book_output_export
    monkeypatch.setattr(book_output_export, "DATA_DIR", tmp_path)
    Image.new("RGB", (600, image_height), "#789081").save(tmp_path / "portrait.jpg")
    title = "河边的小路" * (40 if long_heading else 1)
    layout = configured_pdf_layout(
        PDF_LAYOUTS[edition], template="custom" if long_heading else "classic_family",
        custom_layout={"font_scale": "large", "line_spacing": "relaxed", "page_margin": "wide"} if long_heading else None,
    )
    builder = PDFBookBuilder(title="往事", layout=layout)
    text = "那年我们沿着河边去学校，路上总有说不完的话。" * 15
    _add_chapters(builder, [{
        "title": "我的童年", "sections": [{"section_id": "s", "title": title, "content": text}],
        "illustrations": [{"storage_path": "portrait.jpg", "placement": "chapter_inline", "section_id": "s", "inline_position": "before"}],
    }])
    page = next(page for page in builder.pages if any(image.path.name == "portrait.jpg" for image in page.images))
    headings = [line for item in builder.pages for line in item.commands if line.font_size == layout.font_size + 2]
    assert "".join(line.text for line in headings) == title
    if not long_heading:
        assert headings[-1] in page.commands
    image = next(image for image in page.images if image.path.name == "portrait.jpg")
    assert image.width > 0 and image.height > 0
    assert image.y >= builder.layout.margin_bottom
    assert image.width / image.height == pytest.approx(600 / image_height)
    assert any(line.font_size == builder.layout.font_size for line in page.commands)
    assert "".join(line.text for item in builder.pages for line in item.commands if line.font_size == builder.layout.font_size) == text
    assert all(line.y >= builder.layout.margin_bottom for item in builder.pages for line in item.commands)


def test_multiple_portraits_share_the_text_flow_across_pages(tmp_path):
    path = tmp_path / "portrait.jpg"
    Image.new("RGB", (600, 900), "#789081").save(path)
    builder = PDFBookBuilder(title="往事", layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD])
    builder.new_page("正文", body_numbering=True)
    for _ in range(3):
        builder.add_image(path, wrap_right=True)
    text = "那年我们沿着河边去学校，路上总有说不完的话。" * 50
    builder.add_body_text(text)
    builder.finish_image_wrap()
    assert sum(len(page.images) for page in builder.pages) == 3
    assert "".join(line.text for page in builder.pages for line in page.commands) == text
    for page in builder.pages:
        for image in page.images:
            beside = [line for line in page.commands if line.y + line.font_size > image.y and line.y < image.y + image.height]
            assert beside
            assert all(line.x + _estimated_text_width(line.text, line.font_size) < image.x for line in beside)
