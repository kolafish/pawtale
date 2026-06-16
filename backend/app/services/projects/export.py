from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.token import Token
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.projects import ProjectExportResponse
from app.services import project_documents as project_docs
from app.services.content_storage import read_content
from app.services.projects.access import load_project


class ProjectExportFormat(StrEnum):
    TXT = "txt"
    MARKDOWN = "markdown"


@dataclass(frozen=True)
class ProjectExportFile:
    filename: str
    media_type: str
    content: str


markdown_parser = MarkdownIt("commonmark")
SAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n]+')


def safe_filename(name: str, extension: str) -> str:
    stem = SAFE_FILENAME_RE.sub("-", name).strip(" .-") or "pawtale-export"
    return f"{stem}.{extension}"


def content_disposition(filename: str) -> str:
    quoted = quote(filename)
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "pawtale-export"
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'


async def export_project_data(db: AsyncSession, project_id: str, user_id: str) -> ProjectExportResponse:
    project = await load_project(db, project_id, user_id)
    sections = []
    total = 0
    for group in project.groups:
        docs = []
        for doc in group.documents:
            content = await read_content(project_id, doc.id)
            word_count = project_docs.count_words(content)
            total += word_count
            docs.append({"name": doc.name, "content": content, "word_count": word_count})
        sections.append({"group": group.name, "documents": docs})
    return ProjectExportResponse(title=project.title, sections=sections, total_words=total)


def _inline_text(token: Token) -> str:
    if not token.children:
        return token.content
    parts: list[str] = []
    for child in token.children:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type == "softbreak":
            parts.append("\n")
        elif child.type == "hardbreak":
            parts.append("\n")
        elif child.type == "html_inline":
            continue
        elif child.content:
            parts.append(child.content)
    return "".join(parts)


def markdown_to_plain_text(markdown: str) -> str:
    tokens = markdown_parser.parse(markdown)
    blocks: list[str] = []
    current_lines: list[str] = []

    def flush_current() -> None:
        if current_lines:
            text = "\n".join(line for line in current_lines if line.strip()).strip()
            if text:
                blocks.append(text)
            current_lines.clear()

    for token in tokens:
        if token.type in {"heading_open", "paragraph_open", "blockquote_open", "list_item_open"}:
            flush_current()
            continue
        if token.type in {"heading_close", "paragraph_close", "blockquote_close", "list_item_close"}:
            flush_current()
            continue
        if token.type == "fence":
            flush_current()
            if token.content.strip():
                blocks.append(token.content.strip())
            continue
        if token.type == "code_block":
            flush_current()
            if token.content.strip():
                blocks.append(token.content.strip())
            continue
        if token.type == "hr":
            flush_current()
            blocks.append("----------")
            continue
        if token.type == "inline":
            text = _inline_text(token).strip()
            if text:
                current_lines.append(text)
            continue
        if token.type == "html_block":
            continue
        if token.type == "bullet_list_open" or token.type == "ordered_list_open":
            flush_current()
            continue
        if token.type == "bullet_list_close" or token.type == "ordered_list_close":
            flush_current()
            continue

    flush_current()
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def render_markdown_export(data: ProjectExportResponse) -> str:
    parts = [f"# {data.title}".strip()]
    for section in data.sections:
        group_name = str(section.get("group") or "未命名分组")
        parts.append(f"## {group_name}")
        for doc in section.get("documents", []):
            doc_name = str(doc.get("name") or "未命名文档")
            content = str(doc.get("content") or "").strip()
            parts.append(f"### {doc_name}")
            if content:
                parts.append(content)
    return "\n\n".join(parts).strip() + "\n"


def render_txt_export(data: ProjectExportResponse) -> str:
    parts = [data.title]
    for section in data.sections:
        group_name = str(section.get("group") or "未命名分组")
        section_parts = [f"【{group_name}】"]
        for doc in section.get("documents", []):
            doc_name = str(doc.get("name") or "未命名文档")
            content = markdown_to_plain_text(str(doc.get("content") or "")).strip()
            section_parts.append(f"{doc_name}\n\n{content}" if content else doc_name)
        parts.append("\n\n---\n\n".join(section_parts))
    return "\n\n========\n\n".join(part.strip() for part in parts if part.strip()) + "\n"


def render_export_file(data: ProjectExportResponse, export_format: ProjectExportFormat) -> ProjectExportFile:
    if export_format == ProjectExportFormat.MARKDOWN:
        return ProjectExportFile(
            filename=safe_filename(data.title, "md"),
            media_type="text/markdown; charset=utf-8",
            content=render_markdown_export(data),
        )
    return ProjectExportFile(
        filename=safe_filename(data.title, "txt"),
        media_type="text/plain; charset=utf-8",
        content=render_txt_export(data),
    )
