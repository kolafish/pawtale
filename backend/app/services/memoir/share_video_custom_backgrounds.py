from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

import aiofiles
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import DATA_DIR
from app.domain.memoir.types import MemoirImageAssetSourceType, MemoirImageAssetStatus
from app.models.memoir import MemoirImageAsset
from app.schemas.memoir_voice import MemoirShareVideoCustomBackgroundAssetResponse
from app.services.memoir.access import load_memoir_project


SHARE_VIDEO_CUSTOM_BACKGROUND_VERSION = "memoir-share-video-custom-background-v1"
SHARE_VIDEO_CUSTOM_BACKGROUND_PRESET = "memoir_soft"
SHARE_VIDEO_CUSTOM_BACKGROUND_WIDTH = 1280
SHARE_VIDEO_CUSTOM_BACKGROUND_HEIGHT = 720
SHARE_VIDEO_CUSTOM_BACKGROUND_MAX_BYTES = 10 * 1024 * 1024
SHARE_VIDEO_CUSTOM_BACKGROUND_MAX_PIXELS = 20_000_000
_PROCESSED_MIME_TYPE = "image/jpeg"
_PROCESSED_EXTENSION = "jpg"
_SUPPORTED_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_PROCESSING_JSON = {
    "target_width": SHARE_VIDEO_CUSTOM_BACKGROUND_WIDTH,
    "target_height": SHARE_VIDEO_CUSTOM_BACKGROUND_HEIGHT,
    "fit": "cover",
    "saturation": 0.55,
    "contrast": 0.82,
    "brightness": 0.04,
    "blur": "boxblur=1:1",
    "warm_overlay": "#F6F1E8",
    "warm_overlay_opacity": 0.24,
}


@dataclass(frozen=True)
class _ImageInfo:
    mime_type: str
    extension: str
    width: int
    height: int


def _safe_path_part(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return normalized.strip("-") or "default"


def _source_signature(*parts: object) -> str:
    payload = "\u241f".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _custom_background_root() -> Path:
    return DATA_DIR / "memoir" / "share-video-backgrounds" / "uploads"


def _file_url(project_id: str, asset_id: str) -> str:
    return f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}/file"


def _relative_storage_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(DATA_DIR.resolve()))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="分享视频背景图存储路径无效") from exc


async def _read_upload(upload: UploadFile) -> bytes:
    content = await upload.read(SHARE_VIDEO_CUSTOM_BACKGROUND_MAX_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="请选择要上传的背景图片")
    if len(content) > SHARE_VIDEO_CUSTOM_BACKGROUND_MAX_BYTES:
        raise HTTPException(status_code=400, detail="背景图片不能超过 10MB")
    return content


def _png_size(content: bytes) -> tuple[int, int] | None:
    if len(content) < 24 or not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")


def _jpeg_size(content: bytes) -> tuple[int, int] | None:
    if len(content) < 4 or not content.startswith(b"\xff\xd8"):
        return None
    index = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while index + 9 < len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        while index < len(content) and content[index] == 0xFF:
            index += 1
        if index >= len(content):
            break
        marker = content[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(content):
            break
        length = int.from_bytes(content[index:index + 2], "big")
        if length < 2 or index + length > len(content):
            break
        if marker in sof_markers and length >= 7:
            height = int.from_bytes(content[index + 3:index + 5], "big")
            width = int.from_bytes(content[index + 5:index + 7], "big")
            return width, height
        index += length
    return None


def _webp_size(content: bytes) -> tuple[int, int] | None:
    if len(content) < 30 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
        return None
    position = 12
    while position + 8 <= len(content):
        chunk_type = content[position:position + 4]
        chunk_size = int.from_bytes(content[position + 4:position + 8], "little")
        chunk_start = position + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > len(content):
            return None
        chunk = content[chunk_start:chunk_end]
        if chunk_type == b"VP8X" and len(chunk) >= 10:
            width = int.from_bytes(chunk[4:7], "little") + 1
            height = int.from_bytes(chunk[7:10], "little") + 1
            return width, height
        if chunk_type == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(chunk[6:8], "little") & 0x3FFF
            height = int.from_bytes(chunk[8:10], "little") & 0x3FFF
            return width, height
        if chunk_type == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2F:
            bits = int.from_bytes(chunk[1:5], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return width, height
        position = chunk_end + (chunk_size % 2)
    return None


def _sniff_image(content: bytes) -> _ImageInfo:
    if size := _jpeg_size(content):
        mime_type = "image/jpeg"
    elif size := _png_size(content):
        mime_type = "image/png"
    elif size := _webp_size(content):
        mime_type = "image/webp"
    else:
        raise HTTPException(status_code=400, detail="仅支持 JPEG、PNG 或 WebP 背景图片")
    width, height = size
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="背景图片文件无效")
    if width * height > SHARE_VIDEO_CUSTOM_BACKGROUND_MAX_PIXELS:
        raise HTTPException(status_code=400, detail="背景图片像素过大，请选择 2000 万像素以内的图片")
    return _ImageInfo(mime_type=mime_type, extension=_SUPPORTED_EXTENSIONS[mime_type], width=width, height=height)


def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg
    except Exception:
        return "ffmpeg"
    return imageio_ffmpeg.get_ffmpeg_exe()


async def _process_background_image(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    video_filter = ",".join(
        (
            f"scale={SHARE_VIDEO_CUSTOM_BACKGROUND_WIDTH}:{SHARE_VIDEO_CUSTOM_BACKGROUND_HEIGHT}:"
            "force_original_aspect_ratio=increase",
            f"crop={SHARE_VIDEO_CUSTOM_BACKGROUND_WIDTH}:{SHARE_VIDEO_CUSTOM_BACKGROUND_HEIGHT}",
            "setsar=1",
            "eq=saturation=0.55:contrast=0.82:brightness=0.04",
            "boxblur=1:1",
            "format=rgba",
            "drawbox=x=0:y=0:w=iw:h=ih:color=0xF6F1E8@0.24:t=fill",
            "format=yuvj420p",
        )
    )
    process = await asyncio.create_subprocess_exec(
        _ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        video_filter,
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(target_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0 or not target_path.exists() or target_path.stat().st_size <= 0:
        message = stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:]
        raise HTTPException(status_code=400, detail=message[0] if message else "背景图片处理失败")


def _response(asset: MemoirImageAsset) -> MemoirShareVideoCustomBackgroundAssetResponse:
    metadata = asset.metadata_json or {}
    return MemoirShareVideoCustomBackgroundAssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        status=MemoirImageAssetStatus(asset.status),
        preview_url=_file_url(asset.project_id, asset.id),
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        processing_preset=str(metadata.get("processing_preset") or SHARE_VIDEO_CUSTOM_BACKGROUND_PRESET),
        original_mime_type=str(metadata.get("original_mime_type") or ""),
        original_size_bytes=(
            int(metadata["original_size_bytes"])
            if isinstance(metadata.get("original_size_bytes"), int)
            else None
        ),
    )


async def create_custom_share_video_background_asset(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    upload: UploadFile,
    processing_preset: str = SHARE_VIDEO_CUSTOM_BACKGROUND_PRESET,
    allow_admin: bool = False,
) -> MemoirShareVideoCustomBackgroundAssetResponse:
    normalized_preset = (processing_preset or SHARE_VIDEO_CUSTOM_BACKGROUND_PRESET).strip()
    if normalized_preset != SHARE_VIDEO_CUSTOM_BACKGROUND_PRESET:
        raise HTTPException(status_code=400, detail="未知的背景图处理方式")
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    content = await _read_upload(upload)
    image_info = _sniff_image(content)
    content_hash = hashlib.sha256(content).hexdigest()
    source_signature = _source_signature(
        MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
        "custom_upload",
        content_hash,
        normalized_preset,
    )
    existing = await db.scalar(
        select(MemoirImageAsset)
        .where(
            MemoirImageAsset.project_id == project.id,
            MemoirImageAsset.source_signature == source_signature,
            MemoirImageAsset.status != MemoirImageAssetStatus.DELETED.value,
        )
        .options(selectinload(MemoirImageAsset.placements))
    )
    if existing:
        return _response(existing)

    folder = (
        _custom_background_root()
        / _safe_path_part(user_id)
        / content_hash[:16]
        / _safe_path_part(normalized_preset)
    )
    original_path = folder / f"original.{image_info.extension}"
    processed_path = folder / f"processed.{_PROCESSED_EXTENSION}"
    folder.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(original_path, "wb") as file:
        await file.write(content)
    await _process_background_image(original_path, processed_path)

    metadata = {
        "share_video_background_source": "custom_upload",
        "original_storage_path": _relative_storage_path(original_path),
        "original_mime_type": image_info.mime_type,
        "original_size_bytes": len(content),
        "original_width": image_info.width,
        "original_height": image_info.height,
        "content_hash": content_hash,
        "processing_preset": normalized_preset,
        "processing_version": SHARE_VIDEO_CUSTOM_BACKGROUND_VERSION,
        "processing_json": dict(_PROCESSING_JSON),
    }
    asset = MemoirImageAsset(
        project_id=project.id,
        user_id=user_id,
        status=MemoirImageAssetStatus.READY.value,
        source_type=MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
        source_signature=source_signature,
        storage_path=_relative_storage_path(processed_path),
        thumbnail_storage_path=_relative_storage_path(processed_path),
        mime_type=_PROCESSED_MIME_TYPE,
        size_bytes=processed_path.stat().st_size,
        width=SHARE_VIDEO_CUSTOM_BACKGROUND_WIDTH,
        height=SHARE_VIDEO_CUSTOM_BACKGROUND_HEIGHT,
        title="自定义分享背景",
        caption="用户上传并自动处理为分享视频背景。",
        alt_text="自定义分享视频背景",
        style_id="custom",
        prompt_version=SHARE_VIDEO_CUSTOM_BACKGROUND_VERSION,
        provider="user_upload",
        model="ffmpeg-background-processor",
        metadata_json=metadata,
        include_in_book=False,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return _response(asset)
