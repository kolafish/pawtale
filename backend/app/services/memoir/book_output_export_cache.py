from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from functools import cached_property
from hashlib import sha256
from pathlib import Path
from typing import Callable

from app.services import content_storage
from app.schemas.memoir_book import MemoirBookManuscript
from app.services.file_exports import ExportFile
from app.services.memoir.book_output_export import (
    EXPORT_DATE,
    MemoirBookOutputExportFormat,
    MemoirBookOutputExportLayout,
    _export_title,
    _illustration_file_path,
    book_export_filename,
)

# Bump when rendering or bundled fonts/artwork change.
BOOK_EXPORT_RENDER_REVISION = 21


@dataclass(frozen=True)
class PreparedBookExport:
    owner_id: str
    project_id: str
    version_id: str
    version: int
    title: str
    content: str
    manuscript: MemoirBookManuscript | None
    export_format: MemoirBookOutputExportFormat
    layout: MemoirBookOutputExportLayout
    show_source_appendix: bool
    render_date: str

    @property
    def filename(self) -> str:
        return book_export_filename(
            _export_title(self.title, self.manuscript.model_dump(mode="json") if self.manuscript else {}),
            self.export_format.value, self.layout,
        )

    @cached_property
    def cache_key(self) -> str:
        illustrations = []
        image_hashes: dict[Path, str | None] = {}
        if self.manuscript and self.export_format == MemoirBookOutputExportFormat.PDF:
            for illustration in [
                *([self.manuscript.render_settings.cover_image] if self.manuscript.render_settings.cover_image else []),
                *self.manuscript.image_appendix,
                *(image for chapter in self.manuscript.chapters for image in chapter.illustrations),
            ]:
                path = _illustration_file_path(illustration.model_dump(mode="json"))
                if path and path not in image_hashes:
                    try:
                        image_hashes[path] = sha256(path.read_bytes()).hexdigest()
                    except FileNotFoundError:
                        image_hashes[path] = None
                illustrations.append((illustration.storage_path, image_hashes.get(path) if path else None))
        payload = {
            "revision": BOOK_EXPORT_RENDER_REVISION,
            "owner": self.owner_id, "project": self.project_id, "version_id": self.version_id,
            "version": self.version, "title": self.title, "content": self.content,
            "format": self.export_format, "layout": self.layout,
            "manuscript": self.manuscript.model_dump(mode="json") if self.manuscript else {},
            "show_source_appendix": self.show_source_appendix,
            "illustrations": illustrations, "render_date": self.render_date,
        }
        return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def export_cache_path(prepared: PreparedBookExport, cache_key: str) -> Path:
    return content_storage.PROJECTS_DIR / prepared.project_id / "book-exports" / f"{cache_key}.{prepared.export_format.value}"


def has_cached_export(path: Path, export_format: MemoirBookOutputExportFormat) -> bool:
    try:
        with path.open("rb") as file:
            size = os.fstat(file.fileno()).st_size
            if export_format != MemoirBookOutputExportFormat.PDF:
                return size > 0
            if size < 10 or file.read(5) != b"%PDF-":
                return False
            file.seek(max(0, size - 16))
            return file.read().rstrip().endswith(b"%%EOF")
    except FileNotFoundError:
        return False


def cached_export_content(path: Path, export_format: MemoirBookOutputExportFormat) -> bytes | None:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    if export_format == MemoirBookOutputExportFormat.PDF:
        return content if content.startswith(b"%PDF-") and content.rstrip().endswith(b"%%EOF") else None
    return content if content else None


def render_cached_book_export(
    prepared: PreparedBookExport, cache_key: str, render: Callable[..., ExportFile],
) -> ExportFile:
    path = export_cache_path(prepared, cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    # OS lock covers requests in other processes and remains held if the HTTP wait times out.
    with path.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        content = cached_export_content(path, prepared.export_format)
        if content is None:
            token = EXPORT_DATE.set(prepared.render_date)
            try:
                result = render(
                    title=prepared.title, version=prepared.version, content=prepared.content,
                    manuscript=prepared.manuscript.model_dump(mode="json") if prepared.manuscript else {},
                    export_format=prepared.export_format, layout=prepared.layout,
                    show_source_appendix=prepared.show_source_appendix,
                )
            finally:
                EXPORT_DATE.reset(token)
            content = result.content.encode() if isinstance(result.content, str) else result.content
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
                    temporary = file.name
                    file.write(content)
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        return ExportFile(
            filename=prepared.filename,
            media_type="application/pdf" if prepared.export_format == MemoirBookOutputExportFormat.PDF else "text/plain; charset=utf-8",
            content=content,
        )
