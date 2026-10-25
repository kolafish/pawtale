from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import struct
import uuid
import wave

import aiofiles
import aiofiles.os
from fastapi import HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR
from app.models.memoir import AudioFile


MEMOIR_AUDIO_DIR = DATA_DIR / "memoir" / "audio"
MAX_AUDIO_BYTES = 20 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".pcm"}
_PCM_SAMPLE_RATE = 16000
_PCM_BYTES_PER_SAMPLE = 2


@dataclass(frozen=True)
class StagedAudioFile:
    audio_file: AudioFile
    path: Path


def _storage_path(user_id: str, filename: str) -> str:
    return f"memoir/audio/{user_id}/{filename}"


def audio_file_path(audio_file: AudioFile) -> Path:
    storage_path = Path(audio_file.storage_path)
    try:
        relative_path = storage_path.relative_to(Path("memoir") / "audio")
    except ValueError:
        return DATA_DIR / storage_path
    return MEMOIR_AUDIO_DIR / relative_path


def pcm_wav_bytes(pcm: bytes) -> bytes:
    byte_rate = _PCM_SAMPLE_RATE * _PCM_BYTES_PER_SAMPLE
    block_align = _PCM_BYTES_PER_SAMPLE
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        _PCM_SAMPLE_RATE,
        byte_rate,
        block_align,
        (_PCM_BYTES_PER_SAMPLE * 8),
        b"data",
        len(pcm),
    )
    return header + pcm


async def audio_file_response(
    audio_file: AudioFile, *, include_filename: bool = False,
    headers: dict[str, str] | None = None, missing_detail: str = "音频文件不存在",
) -> Response:
    path = audio_file_path(audio_file)
    if not path.exists():
        raise HTTPException(status_code=404, detail=missing_detail)
    if audio_file.mime_type == "audio/pcm":
        async with aiofiles.open(path, "rb") as file:
            pcm = await file.read()
        response_headers = dict(headers or {})
        if include_filename:
            response_headers["Content-Disposition"] = f'inline; filename="{audio_file.id}.wav"'
        return Response(content=pcm_wav_bytes(pcm), media_type="audio/wav", headers=response_headers)
    return FileResponse(path, media_type=audio_file.mime_type, headers=headers,
        filename=f"{audio_file.id}{Path(audio_file.storage_path).suffix}" if include_filename else None)


def audio_file_duration_seconds(audio_file: AudioFile, path: Path) -> float | None:
    """Return a measured duration, or None when the audio format cannot be measured."""
    stored_duration = audio_file.duration_seconds
    if stored_duration is not None and stored_duration > 0:
        return float(stored_duration)

    mime_type = audio_file.mime_type.split(";", 1)[0].strip().lower()
    if mime_type in {"audio/pcm", "audio/x-pcm"}:
        return path.stat().st_size / (_PCM_SAMPLE_RATE * _PCM_BYTES_PER_SAMPLE)
    if mime_type in {"audio/wav", "audio/x-wav", "audio/wave"}:
        try:
            with wave.open(str(path), "rb") as wav:
                frame_rate = wav.getframerate()
                if frame_rate > 0:
                    return wav.getnframes() / frame_rate
        except (EOFError, OSError, wave.Error):
            return None
    return None


def _validate_duration(duration_seconds: int | None) -> None:
    if duration_seconds is not None and duration_seconds < 0:
        raise HTTPException(status_code=400, detail="音频时长不能为负数")


def _validate_audio_payload(
    *,
    extension: str,
    size_bytes: int,
    duration_seconds: int | None,
) -> None:
    if extension not in _AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 mp3、wav、ogg、pcm 音频文件")
    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="音频文件不能为空")
    if size_bytes > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频文件不能超过 20MB")
    _validate_duration(duration_seconds)


async def _remove_if_exists(path: Path) -> None:
    if path.exists():
        await aiofiles.os.remove(path)


async def stage_audio_bytes(
    db: AsyncSession,
    user_id: str,
    data: bytes,
    *,
    mime_type: str = "audio/pcm",
    extension: str = ".pcm",
    duration_seconds: int | None = None,
) -> StagedAudioFile:
    _validate_audio_payload(
        extension=extension,
        size_bytes=len(data),
        duration_seconds=duration_seconds,
    )

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
    return StagedAudioFile(audio_file=audio_file, path=target_path)


async def stage_audio_path(
    db: AsyncSession,
    user_id: str,
    source_path: Path,
    *,
    mime_type: str = "audio/pcm",
    extension: str = ".pcm",
    duration_seconds: int | None = None,
    move_source: bool = False,
) -> StagedAudioFile:
    stat = await aiofiles.os.stat(source_path)
    _validate_audio_payload(
        extension=extension,
        size_bytes=stat.st_size,
        duration_seconds=duration_seconds,
    )

    audio_id = str(uuid.uuid4())
    filename = f"{audio_id}{extension}"
    user_audio_dir = MEMOIR_AUDIO_DIR / user_id
    user_audio_dir.mkdir(parents=True, exist_ok=True)
    target_path = user_audio_dir / filename

    try:
        if move_source:
            await aiofiles.os.replace(source_path, target_path)
        else:
            async with aiofiles.open(source_path, "rb") as source:
                async with aiofiles.open(target_path, "wb") as output:
                    while chunk := await source.read(_CHUNK_SIZE):
                        await output.write(chunk)
    except Exception:
        await _remove_if_exists(target_path)
        raise

    audio_file = AudioFile(
        id=audio_id,
        user_id=user_id,
        storage_path=_storage_path(user_id, filename),
        mime_type=mime_type,
        size_bytes=stat.st_size,
        duration_seconds=duration_seconds,
    )
    db.add(audio_file)
    return StagedAudioFile(audio_file=audio_file, path=target_path)


async def discard_staged_audio(staged: StagedAudioFile | None) -> None:
    if staged is not None:
        await _remove_if_exists(staged.path)


async def delete_audio_file_paths(audio_files: Iterable[AudioFile]) -> None:
    for audio_file in audio_files:
        await _remove_if_exists(audio_file_path(audio_file))
