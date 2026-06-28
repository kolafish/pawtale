from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.file_exports import ExportFile, safe_filename


PDF_PAGE_WIDTH = 595
PDF_PAGE_HEIGHT = 842

class MemoirBookOutputExportFormat(StrEnum):
    TXT = "txt"
    PDF = "pdf"


class MemoirBookOutputExportLayout(StrEnum):
    STANDARD = "standard"
    LARGE_PRINT = "large_print"


@dataclass(frozen=True)
class PDFLayout:
    font_size: int
    line_height: int
    margin_x: int
    start_y: int
    max_line_units: int
    max_lines_per_page: int
    filename_suffix: str = ""


PDF_LAYOUTS = {
    MemoirBookOutputExportLayout.STANDARD: PDFLayout(
        font_size=13,
        line_height=18,
        margin_x=58,
        start_y=780,
        max_line_units=34,
        max_lines_per_page=40,
    ),
    MemoirBookOutputExportLayout.LARGE_PRINT: PDFLayout(
        font_size=16,
        line_height=24,
        margin_x=64,
        start_y=760,
        max_line_units=26,
        max_lines_per_page=29,
        filename_suffix="打印版",
    ),
}


def _wrap_pdf_line(text: str, max_units: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    current_units = 0.0
    for char in text:
        char_units = 0.55 if char.isascii() else 1.0
        if current and current_units + char_units > max_units:
            lines.append(current)
            current = char
            current_units = char_units
            continue
        current += char
        current_units += char_units
    if current:
        lines.append(current)
    return lines


def _pdf_lines(text: str, layout: PDFLayout) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            lines.append("")
            continue
        lines.extend(_wrap_pdf_line(stripped, layout.max_line_units))
    return lines


def _pdf_hex_text(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


def _pdf_stream(lines: list[str], layout: PDFLayout) -> bytes:
    commands = [
        "BT",
        f"/F1 {layout.font_size} Tf",
        f"{layout.margin_x} {layout.start_y} Td",
    ]
    first = True
    for line in lines:
        if not first:
            commands.append(f"0 -{layout.line_height} Td")
        first = False
        if line:
            commands.append(f"<{_pdf_hex_text(line)}> Tj")
    commands.append("ET")
    return "\n".join(commands).encode("ascii")


def _render_pdf(content: str, layout: PDFLayout) -> bytes:
    lines = _pdf_lines(content.strip(), layout) or ["我的回忆录"]
    pages = [
        lines[index:index + layout.max_lines_per_page]
        for index in range(0, len(lines), layout.max_lines_per_page)
    ]
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    catalog_id = add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_object(b"")
    font_id = add_object(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
        b"/Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >>"
    )
    add_object(
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 5 >> "
        b"/FontDescriptor 5 0 R >>"
    )
    add_object(
        b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 "
        b"/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 880 "
        b"/Descent -120 /CapHeight 880 /StemV 80 >>"
    )

    page_ids: list[int] = []
    for page_lines in pages:
        stream = _pdf_stream(page_lines, layout)
        content_id = add_object(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_id = add_object(
            (
                f"<< /Type /Page /Parent {pages_id} 0 R "
                f"/MediaBox [0 0 {PDF_PAGE_WIDTH} {PDF_PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        page_ids.append(page_id)

    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] /Count {len(page_ids)} >>"
    ).encode("ascii")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(payload)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _book_export_filename(
    title: str,
    version: int,
    extension: str,
    layout: MemoirBookOutputExportLayout,
) -> str:
    suffix = PDF_LAYOUTS[layout].filename_suffix if extension == "pdf" else ""
    parts = [title, f"全书第{version}版", suffix]
    return safe_filename("-".join(part for part in parts if part), extension)


def render_book_output_export(
    *,
    title: str,
    version: int,
    content: str,
    export_format: MemoirBookOutputExportFormat,
    layout: MemoirBookOutputExportLayout = MemoirBookOutputExportLayout.STANDARD,
) -> ExportFile:
    if export_format == MemoirBookOutputExportFormat.PDF:
        pdf_layout = PDF_LAYOUTS.get(layout, PDF_LAYOUTS[MemoirBookOutputExportLayout.STANDARD])
        return ExportFile(
            filename=_book_export_filename(title, version, "pdf", layout),
            media_type="application/pdf",
            content=_render_pdf(content, pdf_layout),
        )
    return ExportFile(
        filename=_book_export_filename(title, version, "txt", MemoirBookOutputExportLayout.STANDARD),
        media_type="text/plain; charset=utf-8",
        content=content + "\n",
    )
