from __future__ import annotations

import asyncio
import logging
import re
import uuid
from io import BytesIO
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR
from app.models.users import User


AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_MAX_PIXELS = 20_000_000
AVATAR_SIZE = 384
AVATAR_JPEG_QUALITY = 88
_AVATAR_FILENAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.jpg$")
_MANAGED_AVATAR_PREFIX = "/api/auth/avatars/"
logger = logging.getLogger(__name__)


def _avatar_root() -> Path:
    return DATA_DIR / "user-avatars"


def avatar_directory(user_id: str) -> Path:
    if not _is_canonical_user_id(user_id):
        raise ValueError("Account id is not a canonical UUID")
    return _avatar_root() / user_id


def _is_canonical_user_id(user_id: str) -> bool:
    try:
        return str(uuid.UUID(user_id)) == user_id
    except ValueError:
        return False


async def _read_avatar(upload: UploadFile) -> bytes:
    content = await upload.read(AVATAR_MAX_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="请选择头像图片")
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="头像图片不能超过 5MB")
    return content


def _render_avatar(content: bytes) -> bytes:
    try:
        with Image.open(BytesIO(content)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > AVATAR_MAX_PIXELS:
                raise HTTPException(status_code=400, detail="头像图片尺寸无效或像素过大")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(
                image,
                (AVATAR_SIZE, AVATAR_SIZE),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=AVATAR_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
            return output.getvalue()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="仅支持有效的头像图片") from exc


def _managed_avatar_path(avatar_url: str | None, expected_user_id: str) -> Path | None:
    if not avatar_url or not avatar_url.startswith(_MANAGED_AVATAR_PREFIX):
        return None
    parts = avatar_url.removeprefix(_MANAGED_AVATAR_PREFIX).split("/")
    if len(parts) != 2:
        return None
    user_id, filename = parts
    if (
        user_id != expected_user_id
        or not _is_canonical_user_id(user_id)
        or not _AVATAR_FILENAME_PATTERN.fullmatch(filename)
    ):
        return None
    return _avatar_root() / user_id / filename


def _remove_avatar(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to remove avatar file path=%s", path, exc_info=True)


def avatar_file_path(user_id: str, filename: str) -> Path:
    if not _is_canonical_user_id(user_id) or not _AVATAR_FILENAME_PATTERN.fullmatch(filename):
        raise HTTPException(status_code=404, detail="头像不存在")
    return _avatar_root() / user_id / filename


async def update_user_avatar(
    db: AsyncSession,
    *,
    user: User,
    upload: UploadFile,
) -> User:
    content = await _read_avatar(upload)
    rendered = await asyncio.to_thread(_render_avatar, content)
    filename = f"{uuid.uuid4().hex}.jpg"
    folder = _avatar_root() / user.id
    path = folder / filename
    folder.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "wb") as file:
        await file.write(rendered)

    previous_path = _managed_avatar_path(user.avatar_url, user.id)
    user.avatar_url = f"{_MANAGED_AVATAR_PREFIX}{user.id}/{filename}"
    try:
        await db.commit()
        await db.refresh(user)
    except Exception:
        _remove_avatar(path)
        raise

    if previous_path and previous_path != path:
        _remove_avatar(previous_path)
    return user
