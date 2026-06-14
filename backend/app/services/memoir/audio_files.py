from pathlib import Path
import uuid

import aiofiles
import aiofiles.os
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR
from app.models.memoir import AudioFile


MEMOIR_AUDIO_DIR = DATA_DIR / "memoir" / "audio"
MAX_AUDIO_BYTES = 20 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_FORMAT_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".pcm": "audio/pcm",
}
_MIME_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/ogg": ".ogg",
    "audio/pcm": ".pcm",
    "audio/x-pcm": ".pcm",
}
_GENERIC_MIME_TYPES = {"", "application/octet-stream"}


def _storage_path(user_id: str, filename: str) -> str:
    return f"memoir/audio/{user_id}/{filename}"


def audio_file_path(audio_file: AudioFile) -> Path:
    storage_path = Path(audio_file.storage_path)
    try:
        relative_path = storage_path.relative_to(Path("memoir") / "audio")
    except ValueError:
        return DATA_DIR / storage_path
    return MEMOIR_AUDIO_DIR / relative_path


def _normalize_audio_type(upload: UploadFile) -> tuple[str, str]:
    content_type = (upload.content_type or "").split(";")[0].lower()
    suffix = Path(upload.filename or "").suffix.lower()

    if content_type in _MIME_EXTENSIONS:
        extension = _MIME_EXTENSIONS[content_type]
        return extension, _FORMAT_MIME_TYPES[extension]
    if content_type in _GENERIC_MIME_TYPES and suffix in _FORMAT_MIME_TYPES:
        return suffix, _FORMAT_MIME_TYPES[suffix]
    raise HTTPException(status_code=400, detail="仅支持 mp3、wav、ogg、pcm 音频文件")


def _validate_duration(duration_seconds: int | None) -> None:
    if duration_seconds is not None and duration_seconds < 0:
        raise HTTPException(status_code=400, detail="音频时长不能为负数")


async def _remove_if_exists(path: Path) -> None:
    if path.exists():
        await aiofiles.os.remove(path)


async def _write_upload(upload: UploadFile, path: Path) -> int:
    size = 0
    try:
        async with aiofiles.open(path, "wb") as output:
            while chunk := await upload.read(_CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise HTTPException(status_code=413, detail="音频文件不能超过 20MB")
                await output.write(chunk)
    except HTTPException:
        await _remove_if_exists(path)
        raise
    except Exception:
        await _remove_if_exists(path)
        raise

    if size == 0:
        await _remove_if_exists(path)
        raise HTTPException(status_code=400, detail="音频文件不能为空")
    return size


async def save_audio_file(
    db: AsyncSession,
    user_id: str,
    upload: UploadFile,
    duration_seconds: int | None = None,
) -> AudioFile:
    extension, mime_type = _normalize_audio_type(upload)
    _validate_duration(duration_seconds)

    audio_id = str(uuid.uuid4())
    filename = f"{audio_id}{extension}"
    user_audio_dir = MEMOIR_AUDIO_DIR / user_id
    user_audio_dir.mkdir(parents=True, exist_ok=True)
    target_path = user_audio_dir / filename

    size_bytes = await _write_upload(upload, target_path)
    audio_file = AudioFile(
        id=audio_id,
        user_id=user_id,
        storage_path=_storage_path(user_id, filename),
        mime_type=mime_type,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
    )
    db.add(audio_file)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        await _remove_if_exists(target_path)
        raise
    await db.refresh(audio_file)
    return audio_file


async def save_audio_bytes(
    db: AsyncSession,
    user_id: str,
    data: bytes,
    *,
    mime_type: str = "audio/pcm",
    extension: str = ".pcm",
    duration_seconds: int | None = None,
) -> AudioFile:
    if extension not in _FORMAT_MIME_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 mp3、wav、ogg、pcm 音频文件")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="音频文件不能为空")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频文件不能超过 20MB")
    _validate_duration(duration_seconds)

    audio_id = str(uuid.uuid4())
    filename = f"{audio_id}{extension}"
    user_audio_dir = MEMOIR_AUDIO_DIR / user_id
    user_audio_dir.mkdir(parents=True, exist_ok=True)
    target_path = user_audio_dir / filename

    try:
        async with aiofiles.open(target_path, "wb") as output:
            await output.write(data)
    except Exception:
        await _remove_if_exists(target_path)
        raise

    audio_file = AudioFile(
        id=audio_id,
        user_id=user_id,
        storage_path=_storage_path(user_id, filename),
        mime_type=mime_type,
        size_bytes=len(data),
        duration_seconds=duration_seconds,
    )
    db.add(audio_file)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        await _remove_if_exists(target_path)
        raise
    await db.refresh(audio_file)
    return audio_file
