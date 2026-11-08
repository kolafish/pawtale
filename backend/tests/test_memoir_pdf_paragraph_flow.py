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
@pytest.mark.parametrize("page_count", [4, 5, 6, 7])
@pytest.mark.parametrize("back_cover", [False, True])
def test_booklet_padding_preserves_pages_and_keeps_back_cover_last(edition, page_count, back_cover):
    builder = PDFBookBuilder(title="往事", layout=PDF_LAYOUTS[edition])
    for index in range(page_count):
        builder.new_page("封底" if back_cover and index == page_count - 1 else "正文")
        builder.add_text(f"原页 {index + 1}")
    original = list(builder.pages)
    pages = builder.finish(back_cover=back_cover)
    assert [p for p in pages if p.commands] == original
    if edition == MemoirBookOutputExportLayout.LARGE_PRINT:
        assert len(pages) == (page_count + 1) // 4 * 4 + 2
        assert (len(pages) - 2) % 4 == 0
        blanks = [p for p in pages if not p.commands]
        assert all(not p.show_footer and not p.header_title and p.body_page_number is None for p in blanks)
        if back_cover:
            assert pages[-1] is original[-1]
    else:
        assert pages == original
    finished_count = len(pages)
    assert len(builder.finish(back_cover=back_cover)) == finished_count


@pytest.mark.parametrize("mode", ["full", "gallery", "pair"])
def test_print_tail_images_grow_without_stretching_or_moving_surrounding_text(tmp_path, mode):
    path = tmp_path / "portrait.jpg"
    Image.new("RGB", (600, 1200), "#789081").save(path)
    layout = PDF_LAYOUTS[MemoirBookOutputExportLayout.LARGE_PRINT]
    builder = PDFBookBuilder(title="往事", layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.add_body_text("图前正文，保留原位。")
    if mode == "full":
        builder.add_image(path, caption="照片说明")
    else:
        count = 2 if mode == "pair" else 1
        builder.add_image_gallery([path] * count, captions=["照片说明"] * count)
    page = builder.current_page
    before = [(im.x + im.width / 2, im.y + im.height, im.height) for im in page.images]
    captions = [c for c in page.commands if c.text == "照片说明"]
    gaps = [im.y - c.y for im, c in zip(page.images, captions)]
    text_y = page.commands[0].y
    builder.new_page("下一章", body_numbering=True)
    for im, (center, top, height), caption, gap in zip(page.images, before, captions, gaps):
        assert height < im.height <= height * 1.25
        assert im.width / im.height == pytest.approx(0.5)
        assert im.x + im.width / 2 == pytest.approx(center)
        assert im.y + im.height == pytest.approx(top)
        assert im.y - caption.y == pytest.approx(gap)
        assert caption.y - caption.font_size * 0.25 >= layout.margin_bottom + layout.line_height
        assert layout.margin_x - layout.inner_margin_delta <= im.x < im.x + im.width <= layout.page_width - layout.margin_x + layout.inner_margin_delta
    assert page.commands[0].y == text_y
    assert len(builder.pages) == 2


@pytest.mark.parametrize("case", ["reading", "following_text", "wrap", "little_space", "full_width"])
def test_images_stay_unchanged_when_tail_enlargement_would_not_fit(tmp_path, case):
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (1200, 600) if case == "full_width" else (600, 1200), "#789081").save(path)
    edition = MemoirBookOutputExportLayout.STANDARD if case == "reading" else MemoirBookOutputExportLayout.LARGE_PRINT
    layout = PDF_LAYOUTS[edition]
    builder = PDFBookBuilder(title="往事", layout=layout)
    builder.new_page("正文", body_numbering=True)
    if case == "little_space":
        builder.y = layout.margin_bottom + 260
    builder.add_image(path, caption="图注", wrap_right=case == "wrap")
    if case == "following_text":
        builder.add_body_text("图片后面仍有正文。")
    page = builder.current_page
    before = [(im.x, im.y, im.width, im.height) for im in page.images]
    builder.new_page("下一章")
    assert [(im.x, im.y, im.width, im.height) for im in page.images] == before


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
    # Enough prose to wrap this tall portrait at both screen and print density.
    text = "那年我们沿着河边去学校，路上总有说不完的话。" * 30
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


@pytest.mark.parametrize("edition", list(MemoirBookOutputExportLayout))
@pytest.mark.parametrize("short_tail", [False, True])
def test_short_prose_groups_portraits_below_text_in_rows(tmp_path, monkeypatch, edition, short_tail):
    from app.services.memoir import book_output_export
    monkeypatch.setattr(book_output_export, "DATA_DIR", tmp_path)
    for index in range(3):
        Image.new("RGB", (600, 1000), "#789081").save(tmp_path / f"{index}.jpg")
    text = "我那年开始工作，后来回到村里。" * 6
    if short_tail:
        text = "我常常回想当时的经历。" * 100 + "\n" + text
    layout = PDF_LAYOUTS[edition]
    builder = PDFBookBuilder(title="往事", layout=layout)
    _add_chapters(builder, [{"title": "工作经历", "sections": [{"section_id": "s", "title": "村里的日子", "content": text}],
        "illustrations": [{"storage_path": f"{i}.jpg", "placement": "chapter_inline", "section_id": "s",
                           "inline_position": "after" if short_tail else "before"} for i in range(3)]}])
    images = [(page, image) for page in builder.pages for image in page.images if image.path.name in {"0.jpg", "1.jpg", "2.jpg"}]
    assert len(images) == 3
    first_page, first = images[0]
    second_page, second = images[1]
    assert first_page is second_page
    assert first.y + first.height == pytest.approx(second.y + second.height)
    assert first.x + first.width < second.x
    assert images[2][1].x + images[2][1].width / 2 == pytest.approx(builder._content_left() + (layout.page_width - 2 * layout.margin_x) / 2)
    for page, image in images:
        assert image.width / image.height == pytest.approx(0.6)
        assert image.y >= layout.margin_bottom
        assert image.y + image.height <= layout.page_height - layout.margin_top
        assert all(line.y > image.y + image.height for line in page.commands if line.font_size == layout.font_size)
    assert "".join(line.text for page in builder.pages for line in page.commands if line.font_size == layout.font_size) == text.replace("\n", "")


@pytest.mark.parametrize("remaining_height", [200, 310])
def test_image_gallery_breaks_by_row_instead_of_moving_the_whole_group(tmp_path, remaining_height):
    path = tmp_path / "image.jpg"
    Image.new("RGB", (600, 1000), "#789081").save(path)
    layout = PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD]
    builder = PDFBookBuilder(title="往事", layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.y = layout.margin_bottom + remaining_height
    builder.add_image_gallery([path] * 7)
    rows_per_page = [len(page.images) for page in builder.pages]
    assert rows_per_page[0] == 2
    assert sum(rows_per_page) == 7
    assert all(image.y >= layout.margin_bottom for page in builder.pages for image in page.images)


def test_long_prose_keeps_multiple_portraits_in_text_flow(tmp_path, monkeypatch):
    from app.services.memoir import book_output_export
    monkeypatch.setattr(book_output_export, "DATA_DIR", tmp_path)
    Image.new("RGB", (600, 1000), "#789081").save(tmp_path / "portrait.jpg")
    builder = PDFBookBuilder(title="往事", layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD])
    _add_chapters(builder, [{"title": "工作经历", "sections": [{"section_id": "s", "title": "村里的日子", "content": "我那年开始工作，后来回到村里。" * 160}],
        "illustrations": [{"storage_path": "portrait.jpg", "placement": "chapter_inline", "section_id": "s", "inline_position": "before"}] * 3}])
    images = [(page, image) for page in builder.pages for image in page.images if image.path.name == "portrait.jpg"]
    assert len(images) == 3
    for page, image in images:
        assert any(line.y > image.y and line.y < image.y + image.height and line.x < image.x
                   for line in page.commands if line.font_size == builder.layout.font_size)


@pytest.mark.parametrize("edition", list(MemoirBookOutputExportLayout))
@pytest.mark.parametrize("mode", ["full", "float", "gallery"])
@pytest.mark.parametrize("caption", ["爸爸和奶奶，1982 年春天摄于老家", "老照片里的家人" * 70])
def test_photo_caption_stays_centered_with_its_image_without_losing_text(tmp_path, edition, mode, caption):
    path = tmp_path / "family.jpg"
    Image.new("RGB", (600, 900), "#eee3ce").save(path)
    layout = PDF_LAYOUTS[edition]
    builder = PDFBookBuilder(title="家人", layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.y = layout.margin_bottom + 55
    if mode == "gallery":
        builder.add_image_gallery([path, path], captions=[caption, "AI 意象插图"])
    else:
        builder.add_image(path, caption=caption, wrap_right=mode == "float")
    builder.finish_image_wrap()
    body = "这些是家里的旧照片。" * 30
    builder.add_body_text(body)
    all_text = "".join(command.text for page in builder.pages for command in page.commands)
    assert caption.replace(" ", "") in all_text.replace(" ", "")
    assert body in all_text
    for page in builder.pages:
        notes = [line for line in page.commands if line.font_size == layout.small_font_size]
        for note in notes:
            center = note.x + _estimated_text_width(note.text, note.font_size) / 2
            images = [image for image in page.images if abs(image.x + image.width / 2 - center) < 1]
            assert images, "Caption must be centered under an image on the same page"
            assert layout.margin_bottom <= note.y < max(image.y for image in images)
        for image in page.images:
            assert image.height > 0 and image.y >= layout.margin_bottom


def test_book_export_shows_photo_notes_and_ai_source_labels(tmp_path, monkeypatch):
    path = tmp_path / "picture.jpg"
    Image.new("RGB", (600, 400), "#eee3ce").save(path)
    monkeypatch.setattr("app.services.memoir.book_output_export._illustration_file_path", lambda _: path)
    builder = PDFBookBuilder(title="家人", layout=PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD])
    _add_chapters(builder, [{"title": "家里的照片", "content": "我们常常翻看旧相册。", "illustrations": [
        {"placement": "chapter_inline", "caption": "爸爸，1982 年", "disclosure": ""},
        {"placement": "chapter_inline", "caption": "生成插图使用的故事原文", "disclosure": "AI 生成"},
    ]}])
    text = "".join(line.text for page in builder.pages for line in page.commands)
    assert "爸爸，1982 年" in text
    assert "AI 意象插图" in text
    assert "生成插图使用的故事原文" not in text


@pytest.mark.parametrize("edition", list(MemoirBookOutputExportLayout))
@pytest.mark.parametrize("template", [
    "classic_family", "photo_memory", "oral_archive", "heritage_classic",
    "warm_journal", "modern_minimal", "custom",
])
@pytest.mark.parametrize("mode", ["full", "half_width", "inline", "float", "gallery"])
def test_max_length_photo_caption_paginates_in_large_type_with_wide_margins(tmp_path, edition, template, mode):
    path = tmp_path / "family.jpg"
    Image.new("RGB", (600, 900), "#eee3ce").save(path)
    layout = configured_pdf_layout(
        PDF_LAYOUTS[edition], template=template,
        custom_layout={"font_scale": "large", "page_margin": "wide"},
    )
    builder = PDFBookBuilder(title="家人", layout=layout)
    builder.new_page("正文", body_numbering=True)
    builder.add_body_text("前面的回忆。")
    builder.y = layout.margin_bottom + 55
    caption = "这张照片里是奶奶和爸爸，摄于家门口的院子里。" * 25
    caption = caption[:497] + "说明完"
    assert len(caption) == 500
    if mode == "gallery":
        builder.add_image_gallery([path, path], captions=[caption, "下一张照片"])
    else:
        builder.add_image(path, caption=caption, wrap_right=mode == "float", display_size=mode)
    builder.finish_image_wrap()
    body = "这些是家里的旧照片。" * 30
    builder.add_body_text(body)
    text = "".join(command.text for page in builder.pages for command in page.commands)
    assert text == "前面的回忆。" + caption + ("下一张照片" if mode == "gallery" else "") + body
    notes = "".join(line.text for page in builder.pages for line in page.commands if line.font_size == layout.small_font_size)
    assert notes == caption + ("下一张照片" if mode == "gallery" else "")
    assert sum(len(page.images) for page in builder.pages) == (2 if mode == "gallery" else 1)
    image_page = next(page for page in builder.pages if page.images)
    assert any(line.font_size == layout.small_font_size for line in image_page.commands)
    for pdf_index, page in enumerate(builder.pages):
        offset = layout.inner_margin_delta * (1 if pdf_index % 2 else -1) if layout.mirror_body_pages else 0
        for image in page.images:
            assert image.height > 0 and image.width > 0
            assert layout.margin_bottom <= image.y < image.y + image.height <= layout.page_height - layout.margin_top
        for line in page.commands:
            assert layout.margin_bottom <= line.y <= layout.page_height - layout.margin_top
            if line.font_size == layout.small_font_size:
                center = line.x + _estimated_text_width(line.text, line.font_size) / 2
                assert center == pytest.approx(layout.page_width / 2 + offset, abs=0.5)
            for image in page.images:
                assert line.y >= image.y + image.height or line.y + line.font_size <= image.y


@pytest.mark.parametrize("edition", list(MemoirBookOutputExportLayout))
def test_featured_family_note_follows_its_chapter(edition):
    builder = PDFBookBuilder(title="回忆录", layout=PDF_LAYOUTS[edition])
    _add_chapters(builder, [{"title": "第一章", "content": "正文", "family_notes": [
        {"display_name": "小姑", "content": "我也记得这件事。"}]}, {"title": "第二章", "content": "下一章的正文"}])
    lines = [command.text for page in builder.pages for command in page.commands if hasattr(command, "text")]
    text = "".join(lines)
    assert text.index("家人留言") < text.index("小姑：我也记得这件事。") < text.index("下一章的正文")
