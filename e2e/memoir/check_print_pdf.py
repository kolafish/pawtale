"""Compare the downloaded PDF with its public manuscript snapshot."""
import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass(frozen=True)
class Line:
    page: int
    text: str
    x: float
    right: float
    y: float
    size: float


def compact(text):
    return "".join(text.split())


def lines_on(page):
    lines = []
    for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in block["lines"]:
            spans = line["spans"]
            text = compact("".join(span["text"] for span in spans))
            if text:
                x, _, right, _ = line["bbox"]
                lines.append(Line(page.number, text, x, right, spans[0]["origin"][1], spans[0]["size"]))
    return sorted(lines, key=lambda line: (line.y, line.x))


def unique_line(lines, text, description):
    matches = [line for line in lines if line.text == compact(text)]
    assert len(matches) == 1, f"{description}: expected one occurrence of {text!r}, found {len(matches)}"
    return matches[0]


def check_toc(pdf, pages, chapters):
    toc = unique_line([line for page in pages for line in page], "目录", "contents")
    rows = pages[toc.page]
    expected_lines = 1 + sum(2 + len(chapter["sections"]) for chapter in chapters)
    assert len(rows) == expected_lines, (
        f"PDF page {toc.page + 1}: contents has {len(rows)} text lines, expected {expected_lines}"
    )
    links = pdf[toc.page].get_links()
    chapter_pages = []
    last_y = toc.y
    for chapter in chapters:
        title = chapter["title"]
        labels = [line for line in rows if line.text.startswith("第") and line.text.endswith(compact(title))]
        assert len(labels) == 1, f"PDF page {toc.page + 1} / {title}: missing or duplicate chapter in contents"
        label = labels[0]
        assert label.y > last_y, f"PDF page {toc.page + 1} / {title}: wrong contents order"
        page_numbers = [line for line in rows if line.text.isdecimal() and abs(line.y - label.y) < 1]
        assert len(page_numbers) == 1, f"PDF page {toc.page + 1} / {title}: missing printed folio"
        number = page_numbers[0]
        # ReportLab may emit a different PDF link rectangle for each text run
        # in the same row. Test each row by where its visible text begins.
        assert 20 < label.x < number.x < pdf[toc.page].rect.width - 20
        targets = row_targets(links, label)
        assert len(targets) == 1, f"PDF page {toc.page + 1} / {title}: invalid chapter link {targets}"
        (target,) = targets
        assert target > toc.page and target % 2 == 1, f"{title}: chapter starts on PDF page {target + 1}"
        assert not chapter_pages or target > chapter_pages[-1], f"{title}: chapters out of order in PDF"
        unique_line([line for line in pages[target] if 50 < line.y < 540], title,
                    f"PDF page {target + 1} / chapter destination")
        folio = unique_line([line for line in pages[target] if line.y > 550], number.text,
                            f"PDF page {target + 1} / printed folio")
        assert row_targets(links, number) == {target}, f"{title}: printed number links to the wrong page"
        assert folio.x > pdf[target].rect.width / 2, f"PDF page {target + 1}: folio must be on outer edge"
        last_y = label.y
        for section in chapter["sections"]:
            row = unique_line(rows, section["title"], f"PDF page {toc.page + 1} / {title} contents")
            assert row.y > last_y and row_targets(links, row) == {target}, f"{title} / {row.text}: section contents"
            last_y = row.y
        chapter_pages.append(target)
    return toc.page, chapter_pages


def row_targets(links, line):
    targets = set()
    for link in links:
        if not link["from"].contains((line.x + 3, line.y - 2)):
            continue
        if link["kind"] == pymupdf.LINK_GOTO:
            targets.add(link["page"])
        elif link["kind"] == pymupdf.LINK_NAMED and link.get("view") == "Fit" and str(link.get("page")).isdecimal():
            # MuPDF returns an internal ReportLab /Fit destination as a
            # one-based string, unlike LINK_GOTO's zero-based integer.
            targets.add(int(link["page"]) - 1)
    return targets


def check_paragraphs(pdf, lines, section, chapter):
    title = section["title"]
    assert lines, f"{chapter} / {title}: no PDF body text"
    paragraphs = [compact(text) for text in section["content"].splitlines() if text.strip()]
    paragraph_starts = {}
    offset = 0
    for index, text in enumerate(paragraphs, 1):
        paragraph_starts[offset] = index
        offset += len(text)
    expected = "".join(paragraphs)
    offset = 0
    positions = []
    continuation_pages = set()
    previous = None
    for line in lines:
        width = pdf[line.page].rect.width
        right_page = line.page % 2 == 1
        left, right = (52, 40) if right_page else (40, 52)
        paragraph_number = paragraph_starts.get(offset)
        situation = f"PDF page {line.page + 1} / {chapter} / {title} / paragraph {len(positions) + (paragraph_number is not None)}"
        # The PDF bounding box includes a full glyph advance for punctuation.
        # Allow one body glyph at the nominal right boundary.
        assert 50 <= line.y <= 545 and line.right <= width - right + line.size, f"{situation}: outside print margins"
        if previous and previous.page == line.page:
            assert line.y - previous.y >= line.size, f"{situation}: overlapping lines"
        assert expected.startswith(line.text, offset), (
            f"{situation}: expected {expected[offset:offset + len(line.text)]!r}, found {line.text!r}"
        )
        expected_x = left + (2 * line.size if paragraph_number else 0)
        assert abs(line.x - expected_x) <= 2, f"{situation}: wrong paragraph indent at x={line.x:.1f}"
        if paragraph_number:
            positions.append({"paragraph": paragraph_number, "page": line.page + 1, "y": round(line.y, 1)})
        elif previous and previous.page != line.page:
            continuation_pages.add(line.page)
        offset += len(line.text)
        previous = line
    assert offset == len(expected) and len(positions) == len(paragraphs), (
        f"PDF page {lines[-1].page + 1} / {chapter} / {title}: missing text at offset {offset}, "
        f"found {len(positions)}/{len(paragraphs)} paragraph starts"
    )
    return positions, continuation_pages


def audit(pdf, book):
    chapters = book["manuscript"]["chapters"]
    assert (len(pdf) - 2) % 4 == 0, "The separately printed covers must surround a complete booklet interior"
    for page in pdf:
        assert abs(page.rect.width - 419.53) < 0.5 and abs(page.rect.height - 595.28) < 0.5 and page.rotation == 0, (
            f"PDF page {page.number + 1}: expected portrait A5"
        )
    pages = [lines_on(page) for page in pdf]
    assert any(compact(book["manuscript"]["profile"]["title"]) in line.text for line in pages[0]), "Missing front cover"
    assert pages[-1] or pdf[-1].get_image_info(), "Missing back cover"
    toc, chapter_pages = check_toc(pdf, pages, chapters)
    afterword = unique_line(
        [line for page in pages[chapter_pages[-1]:] for line in page if 50 < line.y < 540], "后记", "afterword",
    )
    continuations = set()
    report = []
    for chapter, start, end in zip(chapters, chapter_pages, [*chapter_pages[1:], afterword.page], strict=True):
        chapter_lines = [line for page in pages[start:end] for line in page if 50 < line.y < 550]
        if end == afterword.page:
            chapter_lines.extend(line for line in pages[end] if 50 < line.y < afterword.y)
        titles = [unique_line(chapter_lines, item["title"], f"{chapter['title']} heading") for item in chapter["sections"]]
        boundaries = [chapter_lines.index(line) for line in titles] + [len(chapter_lines)]
        assert sorted(boundaries) == boundaries, f"{chapter['title']}: wrong heading order"
        sections = []
        for index, section in enumerate(chapter["sections"]):
            heading = titles[index]
            body = chapter_lines[boundaries[index] + 1:boundaries[index + 1]]
            assert body and body[0].page == heading.page, (
                f"PDF page {heading.page + 1} / {chapter['title']} / {heading.text}: heading separated from body"
            )
            positions, continuation_pages = check_paragraphs(pdf, body, section, chapter["title"])
            continuations.update(continuation_pages)
            sections.append({"title": section["title"], "heading_page": heading.page + 1, "paragraphs": positions})
        report.append({"title": chapter["title"], "page": start + 1, "sections": sections})
    assert continuations, "Fixture did not exercise a paragraph crossing a page break"
    for index in range(chapter_pages[0], afterword.page):
        if not pages[index]:
            assert index + 1 in chapter_pages and not pdf[index].get_image_info(), (
                f"PDF page {index + 1}: unexpected blank page inside the body"
            )
    font_ids = {font[0] for index in range(chapter_pages[0], afterword.page) for font in pdf[index].get_fonts()}
    assert font_ids and all(pdf.extract_font(font)[3] for font in font_ids), "Body fonts must be embedded"
    assert not any("\ufffd" in line.text for page in pages for line in page), "PDF has unreadable glyphs"
    return {
        "pages": len(pdf), "contents_page": toc + 1, "chapters": report,
        "paragraph_continuation_pages": [index + 1 for index in sorted(continuations)],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    report = {}
    for basename in ("before-correction", "final"):
        try:
            book = json.loads((directory / f"{basename}.json").read_text())
            with pymupdf.open(directory / f"{basename}.pdf") as pdf:
                try:
                    report[basename] = audit(pdf, book)
                finally:
                    previews = directory / basename
                    previews.mkdir(exist_ok=True)
                    for page in pdf:
                        page.get_pixmap(dpi=110, alpha=False).save(previews / f"page-{page.number + 1:02}.png")
        except (AssertionError, OSError, ValueError) as error:
            report[basename] = {"error": str(error)}
        print(f"{basename}: " + (
            report[basename]["error"] if "error" in report[basename] else f"{report[basename]['pages']} pages verified"
        ), flush=True)
    (directory / "pdf-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return int(any("error" in result for result in report.values()))


if __name__ == "__main__":
    raise SystemExit(main())
