"""Step TTS Mini adapter for persisted, long-form narration."""
from __future__ import annotations

import base64
import binascii
import logging
from typing import Literal

from fastapi import HTTPException
import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.services.ai_credits import assert_current_ai_credit_available
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_tts_codec import effective_tts_speed_ratio
from app.services.memoir.voice_tts_types import TTSClient, TTSResult, TTS_UNAVAILABLE_MESSAGE
from app.services.provider_limits import provider_concurrency_limiter


logger = logging.getLogger(__name__)
LEGACY_NARRATION_VOICES = {
    "zh_female_vv_uranus_bigtts": "wenrounvsheng",
    "zh_female_xiaohe_uranus_bigtts": "linjiajiejie",
    "zh_male_m191_uranus_bigtts": "cixingnansheng",
}
NARRATION_VOICES = frozenset(LEGACY_NARRATION_VOICES.values())


class _SubtitleWord(BaseModel):
    text: str = Field(min_length=1)
    start_time: int = Field(ge=0)
    end_time: int = Field(ge=0)


class _Subtitle(BaseModel):
    text: str
    items: list[_SubtitleWord]


class _SpeechEvent(BaseModel):
    type: Literal["speech.audio.delta", "speech.audio.done", "response.subtitle", "speech.audio.error", "error"]
    audio: str = ""
    data: _Subtitle | None = None


class StepFunTTSClient(TTSClient):
    provider_name = "stepfun"
    model_name = "step-tts-mini"
    default_voice = "wenrounvsheng"
    sample_rate = 24000

    def __init__(self, settings: Settings):
        self.api_key = settings.stepfun_api_key.strip()
        self.endpoint = settings.stepfun_base_url.rstrip("/") + "/v1/audio/speech"
        self.timeout_seconds = settings.stepfun_tts_timeout_seconds
        self.max_concurrent_requests = settings.tts_max_concurrent_requests

    def effective_voice(self, voice: str | None = None) -> str:
        requested = (voice or "").strip() or self.default_voice
        # Older released mini-programs still send the three Doubao voice IDs.
        resolved = LEGACY_NARRATION_VOICES.get(requested, requested)
        if resolved not in NARRATION_VOICES:
            raise HTTPException(400, "不支持的朗读音色，请重新选择")
        return resolved

    async def synthesize(
        self, text: str, *, voice: str | None = None, speed_ratio: float | None = None,
    ) -> TTSResult:
        if not self.api_key:
            raise HTTPException(503, "StepFun TTS API key 未配置")
        if not text.strip() or len(text) > 1000:
            raise HTTPException(400, "朗读片段须为 1 至 1000 个字符")
        resolved_voice = self.effective_voice(voice)
        speed = effective_tts_speed_ratio(speed_ratio)
        payload = {
            "model": self.model_name, "input": text, "voice": resolved_voice,
            "response_format": "mp3", "sample_rate": self.sample_rate,
            "speed": speed, "volume": 1.0, "text_normalization": "standard",
            "stream_format": "sse", "timestamp": True,
        }

        async def request() -> TTSResult:
            await assert_current_ai_credit_available()
            audio = bytearray()
            alignment: list[TTSAlignmentSegment] = []
            cursor = 0
            completed = False
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream(
                    "POST", self.endpoint, json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}", "Accept": "text/event-stream"},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        event = _SpeechEvent.model_validate_json(data)
                        if event.type in {"speech.audio.error", "error"}:
                            raise ValueError("StepFun TTS returned an error event")
                        if event.type == "speech.audio.delta":
                            audio.extend(base64.b64decode(event.audio, validate=True))
                        elif event.type == "speech.audio.done":
                            completed = True
                        elif event.type == "response.subtitle":
                            if event.data is None:
                                raise ValueError("StepFun TTS subtitle is missing")
                            for word in event.data.items:
                                start = text.find(word.text, cursor)
                                if start < 0 or word.end_time < word.start_time:
                                    raise ValueError("StepFun TTS subtitle does not match the input")
                                cursor = start + len(word.text)
                                alignment.append(TTSAlignmentSegment(
                                    text=word.text, text_start=start, text_end=cursor,
                                    start_ms=word.start_time,
                                    # Real responses can contain zero-duration words, e.g. 年.
                                    end_ms=max(word.start_time + 1, word.end_time),
                                ))
            if not completed or not audio or not alignment:
                raise ValueError("StepFun TTS response is incomplete")
            return TTSResult(
                audio=bytes(audio), mime_type="audio/mpeg", extension=".mp3",
                provider=self.provider_name, model=self.model_name, voice=resolved_voice,
                speed_ratio=speed, alignment=alignment,
                duration_seconds=max(item.end_ms for item in alignment) / 1000,
            )

        try:
            return await provider_concurrency_limiter.run("tts", self.max_concurrent_requests, request)
        except (httpx.HTTPError, ValidationError, ValueError, binascii.Error) as exc:
            logger.warning("StepFun TTS failed error_type=%s", type(exc).__name__)
            raise HTTPException(503, TTS_UNAVAILABLE_MESSAGE) from exc
