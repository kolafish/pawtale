from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class ExportFile:
    filename: str
    media_type: str
    content: str | bytes


SAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n]+')
SAFE_PATH_PART_RE = re.compile(r"[^a-zA-Z0-9._\-\u4e00-\u9fff]+")


def safe_filename(name: str, extension: str) -> str:
    stem = SAFE_FILENAME_RE.sub("-", name).strip(" .-") or "pawtale-export"
    return f"{stem}.{extension}"


def safe_path_part(name: str) -> str:
    return SAFE_PATH_PART_RE.sub("-", name).strip(" .-") or "untitled"


def content_disposition(filename: str) -> str:
    quoted = quote(filename)
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "pawtale-export"
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'
