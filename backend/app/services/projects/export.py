from __future__ import annotations

import json
from enum import StrEnum
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from markdown_it import MarkdownIt
from markdown_it.token import Token
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.projects import ProjectExportResponse
from app.services.file_exports import (
    ExportFile as ProjectExportFile,
    safe_filename,
    safe_path_part,
)
import app.services.project_documents as project_docs
from app.services.content_storage import read_content
from app.services.projects.access import load_project


class ProjectExportFormat(StrEnum):
    TXT = "txt"
    MARKDOWN = "markdown"
    ZIP = "zip"


markdown_parser = MarkdownIt("commonmark")


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
            docs.append(
                {
                    "id": doc.id,
                    "name": doc.name,
                    "content": content,
                    "word_count": word_count,
                    "status": doc.status,
                    "label": doc.label,
                    "sort_order": doc.sort_order,
                }
            )
        sections.append({"group": group.name, "documents": docs})

    references = [
        {
            "id": ref.id,
            "type": ref.type,
            "name": ref.name,
            "content": ref.content,
            "metadata_json": ref.metadata_json,
            "sort_order": ref.sort_order,
            "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
        }
        for ref in sorted(project.references, key=lambda item: (item.sort_order, item.name, item.id))
    ]

    return ProjectExportResponse(title=project.title, sections=sections, total_words=total, references=references)


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


def project_manifest(data: ProjectExportResponse) -> dict:
    return {
        "format": "pawtale-project-export-v1",
        "title": data.title,
        "total_words": data.total_words,
        "sections": [
            {
                "group": section.get("group"),
                "documents": [
                    {
                        "id": doc.get("id"),
                        "name": doc.get("name"),
                        "word_count": doc.get("word_count", 0),
                        "status": doc.get("status"),
                        "label": doc.get("label"),
                        "sort_order": doc.get("sort_order", 0),
                    }
                    for doc in section.get("documents", [])
                ],
            }
            for section in data.sections
        ],
        "references": [
            {
                "id": ref.get("id"),
                "type": ref.get("type"),
                "name": ref.get("name"),
                "sort_order": ref.get("sort_order", 0),
                "metadata_json": ref.get("metadata_json", {}),
            }
            for ref in data.references
        ],
    }


def render_zip_export(data: ProjectExportResponse) -> bytes:
    buffer = BytesIO()
    root = safe_path_part(data.title)
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{root}/pawtale-project.json",
            json.dumps(project_manifest(data), ensure_ascii=False, indent=2) + "\n",
        )
        archive.writestr(f"{root}/manuscript/full.md", render_markdown_export(data))
        archive.writestr(f"{root}/manuscript/full.txt", render_txt_export(data))

        for group_index, section in enumerate(data.sections, start=1):
            group_name = str(section.get("group") or "未命名分组")
            group_dir = f"{group_index:03d}-{safe_path_part(group_name)}"
            for doc_index, doc in enumerate(section.get("documents", []), start=1):
                doc_name = str(doc.get("name") or "未命名文档")
                content = str(doc.get("content") or "")
                doc_id = str(doc.get("id") or "").strip()
                doc_suffix = f"-{doc_id}" if doc_id else ""
                archive.writestr(
                    f"{root}/manuscript/{group_dir}/{doc_index:03d}-{safe_path_part(doc_name)}{doc_suffix}.md",
                    content,
                )

        for ref_index, ref in enumerate(data.references, start=1):
            ref_name = str(ref.get("name") or "未命名资料")
            ref_id = str(ref.get("id") or "").strip()
            ref_suffix = f"-{ref_id}" if ref_id else ""
            archive.writestr(
                f"{root}/references/{ref_index:03d}-{safe_path_part(ref_name)}{ref_suffix}.json",
                json.dumps(ref, ensure_ascii=False, indent=2) + "\n",
            )
    return buffer.getvalue()


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
    if export_format == ProjectExportFormat.ZIP:
        return ProjectExportFile(
            filename=safe_filename(data.title, "zip"),
            media_type="application/zip",
            content=render_zip_export(data),
        )
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
