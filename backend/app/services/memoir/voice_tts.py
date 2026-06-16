from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import AudioFile, MemoirChatMessage
from app.models.projects import Project
from app.schemas.memoir import MemoirAssistantTTSRequest, MemoirAssistantTTSResponse
from app.services.memoir.audio_files import discard_staged_audio, stage_audio_bytes


logger = logging.getLogger(__name__)
TTS_UNAVAILABLE_MESSAGE = "语音合成服务暂时不可用，请稍后重试。"
SUPPORTED_RESPONSE_FORMATS = {
    "mp3": ("audio/mpeg", ".mp3"),
    "wav": ("audio/wav", ".wav"),
    "ogg": ("audio/ogg", ".ogg"),
}


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime_type: str
    extension: str
    provider: str
    model: str


@dataclass(frozen=True)
class AudioSpeechProviderConfig:
    provider_name: str
    api_key: str
    base_url: str
    model: str
    voice: str
    response_format: str
    timeout_seconds: float


class TTSClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        ...

    async def synthesize(self, text: str, *, voice: str | None = None) -> TTSResult:
        ...


class AudioSpeechTTSClient:
    """Client for providers exposing an OpenAI-compatible /audio/speech API."""

    def __init__(self, config: AudioSpeechProviderConfig):
        self.config = config
        self.provider_name = config.provider_name
        self.model_name = config.model

    def _endpoint(self) -> str:
        base_url = self.config.base_url.strip().rstrip("/")
        if not base_url:
            base_url = "https://api.openai.com/v1"
        if base_url.endswith("/audio/speech"):
            return base_url
        return f"{base_url}/audio/speech"

    async def synthesize(self, text: str, *, voice: str | None = None) -> TTSResult:
        api_key = self.config.api_key.strip()
        if not api_key:
            raise HTTPException(status_code=503, detail=f"{self.provider_name} TTS API key 未配置")
        response_format = self.config.response_format.strip().lower() or "mp3"
        if response_format not in SUPPORTED_RESPONSE_FORMATS:
            raise HTTPException(status_code=500, detail=f"不支持的 TTS 音频格式: {response_format}")

        payload = {
            "model": self.config.model,
            "input": text,
            "voice": voice or self.config.voice,
            "response_format": response_format,
        }
        timeout = max(1.0, float(self.config.timeout_seconds))
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self._endpoint(),
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.exception("TTS provider rejected request status=%s", exc.response.status_code)
            raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc
        except httpx.HTTPError as exc:
            logger.exception("TTS provider request failed")
            raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc

        audio = response.content
        if not audio:
            raise HTTPException(status_code=502, detail="TTS 未返回可用音频")
        mime_type, extension = SUPPORTED_RESPONSE_FORMATS[response_format]
        return TTSResult(
            audio=audio,
            mime_type=mime_type,
            extension=extension,
            provider=self.provider_name,
            model=self.model_name,
        )


class OpenAICompatibleTTSClient(AudioSpeechTTSClient):
    def __init__(self, settings: Settings):
        super().__init__(
            AudioSpeechProviderConfig(
                provider_name="openai_compatible",
                api_key=settings.tts_api_key,
                base_url=settings.tts_base_url,
                model=settings.tts_model,
                voice=settings.tts_voice,
                response_format=settings.tts_response_format,
                timeout_seconds=settings.tts_timeout_seconds,
            )
        )


def _stepfun_base_url(base_url: str) -> str:
    normalized = (base_url or "https://api.stepfun.ai/v1").strip().rstrip("/")
    if normalized.endswith("/audio/speech") or normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


class StepFunTTSClient(AudioSpeechTTSClient):
    def __init__(self, settings: Settings):
        super().__init__(
            AudioSpeechProviderConfig(
                provider_name="stepfun",
                api_key=settings.stepfun_api_key or settings.tts_api_key,
                base_url=_stepfun_base_url(settings.stepfun_base_url),
                model=settings.stepfun_tts_model or settings.tts_model or "step-tts-2",
                voice=settings.stepfun_tts_voice or settings.tts_voice or "cixingnansheng",
                response_format=settings.tts_response_format,
                timeout_seconds=settings.tts_timeout_seconds,
            )
        )


def get_tts_client(settings: Settings | None = None) -> TTSClient:
    current_settings = settings or get_settings()
    provider = current_settings.tts_provider.strip().lower()
    if provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleTTSClient(current_settings)
    if provider == "stepfun":
        return StepFunTTSClient(current_settings)
    raise HTTPException(status_code=503, detail="TTS_PROVIDER 未启用")


async def _load_assistant_message(
    db: AsyncSession,
    voice_session_id: str,
    message_id: str,
    user_id: str,
) -> MemoirChatMessage:
    result = await db.execute(
        select(MemoirChatMessage)
        .join(Project, MemoirChatMessage.project_id == Project.id)
        .where(
            MemoirChatMessage.id == message_id,
            MemoirChatMessage.voice_session_id == voice_session_id,
            Project.user_id == user_id,
        )
        .options(selectinload(MemoirChatMessage.audio_file))
    )
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="聊天消息不存在")
    if message.role != MemoirChatMessageRole.ASSISTANT.value:
        raise HTTPException(status_code=400, detail="只有 AI 追问可以生成语音")
    if not message.content_text.strip():
        raise HTTPException(status_code=400, detail="AI 追问内容为空")
    return message


async def _load_existing_audio(db: AsyncSession, message: MemoirChatMessage) -> AudioFile | None:
    if not message.audio_file_id:
        return None
    result = await db.execute(select(AudioFile).where(AudioFile.id == message.audio_file_id))
    return result.scalar_one_or_none()


async def synthesize_assistant_message(
    db: AsyncSession,
    voice_session_id: str,
    message_id: str,
    user_id: str,
    req: MemoirAssistantTTSRequest,
    tts_client: TTSClient | None = None,
) -> MemoirAssistantTTSResponse:
    message = await _load_assistant_message(db, voice_session_id, message_id, user_id)
    existing_audio = await _load_existing_audio(db, message)
    if existing_audio and not req.force_regenerate:
        return MemoirAssistantTTSResponse(
            message=message,
            audio_file_id=existing_audio.id,
            provider=str(message.metadata_json.get("tts_provider") or "stored"),
            model=str(message.metadata_json.get("tts_model") or ""),
            mime_type=existing_audio.mime_type,
        )

    client = tts_client or get_tts_client()
    try:
        result = await client.synthesize(message.content_text.strip(), voice=req.voice)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("TTS synthesis failed provider=%s model=%s", client.provider_name, client.model_name)
        raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc

    staged_audio = await stage_audio_bytes(
        db,
        user_id,
        result.audio,
        mime_type=result.mime_type,
        extension=result.extension,
    )
    try:
        message.audio_file_id = staged_audio.audio_file.id
        message.metadata_json = {
            **(message.metadata_json or {}),
            "tts_provider": result.provider,
            "tts_model": result.model,
            "tts_voice": req.voice,
            "tts_mime_type": result.mime_type,
        }
        db.add(message)
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio(staged_audio)
        raise

    await db.refresh(message)
    await db.refresh(staged_audio.audio_file)
    return MemoirAssistantTTSResponse(
        message=message,
        audio_file_id=staged_audio.audio_file.id,
        provider=result.provider,
        model=result.model,
        mime_type=staged_audio.audio_file.mime_type,
    )
