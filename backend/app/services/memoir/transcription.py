import logging

import aiofiles
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import AudioFile
from app.schemas.memoir import MemoirAudioTranscriptionResponse
from app.services.asr import ASRAudioFormat, StepFunASRError, StepFunASRService
from app.services.memoir.access import load_audio_file
from app.services.memoir.audio_files import audio_file_path


logger = logging.getLogger(__name__)

_MIME_FORMATS = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/ogg": "ogg",
}


def _asr_format(audio_file: AudioFile) -> ASRAudioFormat:
    mime_type = audio_file.mime_type.lower()
    audio_format = _MIME_FORMATS.get(mime_type)
    if audio_format:
        return ASRAudioFormat(type=audio_format)
    if mime_type in {"audio/pcm", "audio/x-pcm"}:
        raise HTTPException(status_code=400, detail="PCM 音频暂不支持转写")
    raise HTTPException(status_code=400, detail="音频格式暂不支持转写")


async def _read_audio_bytes(audio_file: AudioFile) -> bytes:
    path = audio_file_path(audio_file)
    if not path.exists():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    async with aiofiles.open(path, "rb") as file:
        return await file.read()


async def transcribe_audio_file(
    db: AsyncSession,
    audio_file_id: str,
    user_id: str,
) -> MemoirAudioTranscriptionResponse:
    audio_file = await load_audio_file(db, audio_file_id, user_id)
    audio_format = _asr_format(audio_file)
    audio_bytes = await _read_audio_bytes(audio_file)

    try:
        result = await StepFunASRService().transcribe(audio_bytes, audio_format)
    except StepFunASRError:
        logger.exception("StepFun ASR failed for audio file %s", audio_file.id)
        raise HTTPException(status_code=503, detail="语音转写服务暂时不可用，请稍后重试。") from None

    text = result.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="未识别到语音内容")

    return MemoirAudioTranscriptionResponse(
        audio_file_id=audio_file.id,
        text=text,
        usage=result.usage,
    )
