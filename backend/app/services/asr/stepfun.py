from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from typing import Any, Literal

import httpx

from app.config import get_settings


settings = get_settings()
AudioFormatType = Literal["mp3", "wav", "ogg", "pcm"]
_SUPPORTED_FORMATS = {"mp3", "wav", "ogg", "pcm"}


class StepFunASRError(RuntimeError):
    pass


@dataclass(frozen=True)
class ASRAudioFormat:
    type: AudioFormatType
    codec: str | None = None
    rate: int | None = None
    bits: int | None = None
    channel: int | None = None

    def to_payload(self) -> dict[str, Any]:
        if self.type not in _SUPPORTED_FORMATS:
            raise ValueError("仅支持 mp3、wav、ogg、pcm 音频格式")
        if self.type == "pcm" and not all((self.codec, self.rate, self.bits, self.channel)):
            raise ValueError("PCM 音频必须提供 codec、rate、bits 和 channel")

        payload: dict[str, Any] = {"type": self.type}
        for key in ("codec", "rate", "bits", "channel"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


@dataclass(frozen=True)
class ASRTranscriptionResult:
    text: str
    deltas: tuple[str, ...]
    usage: dict[str, Any] | None = None


class StepFunASRService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.stepfun_api_key
        self.base_url = (base_url or settings.stepfun_base_url).rstrip("/")
        self.model = model or settings.stepfun_asr_model

    async def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: ASRAudioFormat,
        *,
        language: str = "zh",
        enable_itn: bool = True,
        enable_timestamp: bool = False,
        hotwords: list[str] | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> ASRTranscriptionResult:
        if not self.api_key:
            raise StepFunASRError("StepFun ASR API key is not configured")
        if not audio_bytes:
            raise ValueError("音频内容不能为空")

        payload = self._payload(
            audio_bytes,
            audio_format,
            language=language,
            enable_itn=enable_itn,
            enable_timestamp=enable_timestamp,
            hotwords=hotwords,
        )
        request_timeout = timeout if timeout is not None else settings.stepfun_asr_timeout_seconds

        if client:
            return await self._transcribe_with_client(client, payload, request_timeout)

        async with httpx.AsyncClient() as http_client:
            return await self._transcribe_with_client(http_client, payload, request_timeout)

    def _payload(
        self,
        audio_bytes: bytes,
        audio_format: ASRAudioFormat,
        *,
        language: str,
        enable_itn: bool,
        enable_timestamp: bool,
        hotwords: list[str] | None,
    ) -> dict[str, Any]:
        transcription: dict[str, Any] = {
            "language": language,
            "model": self.model,
            "enable_itn": enable_itn,
            "enable_timestamp": enable_timestamp,
        }
        if hotwords:
            transcription["hotwords"] = hotwords

        return {
            "audio": {
                "data": base64.b64encode(audio_bytes).decode("ascii"),
                "input": {
                    "transcription": transcription,
                    "format": audio_format.to_payload(),
                },
            },
        }

    async def _transcribe_with_client(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        timeout: float,
    ) -> ASRTranscriptionResult:
        try:
            response = await client.post(
                f"{self.base_url}/v1/audio/asr/sse",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise StepFunASRError(f"StepFun ASR request failed: HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise StepFunASRError("StepFun ASR request failed") from exc

        return await _parse_transcription_events(response)


async def _parse_transcription_events(response: httpx.Response) -> ASRTranscriptionResult:
    deltas: list[str] = []
    done_text = ""
    usage: dict[str, Any] | None = None
    current_event: str | None = None
    data_lines: list[str] = []

    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line:
            event = _load_sse_event(data_lines, current_event)
            if event:
                done_text, usage = _consume_event(event, deltas, done_text, usage)
            current_event = None
            data_lines = []
            continue

        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
        elif line.startswith("{"):
            data_lines.append(line)

    event = _load_sse_event(data_lines, current_event)
    if event:
        done_text, usage = _consume_event(event, deltas, done_text, usage)

    return ASRTranscriptionResult(
        text=done_text or "".join(deltas),
        deltas=tuple(deltas),
        usage=usage,
    )


def _load_sse_event(data_lines: list[str], event_type: str | None) -> dict[str, Any] | None:
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    if raw == "[DONE]":
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StepFunASRError("StepFun ASR returned malformed SSE data") from exc
    if not isinstance(event, dict):
        raise StepFunASRError("StepFun ASR returned unsupported SSE data")
    if event_type and "type" not in event:
        event["type"] = event_type
    return event


def _consume_event(
    event: dict[str, Any],
    deltas: list[str],
    done_text: str,
    usage: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    event_type = event.get("type")
    if event_type == "transcript.text.delta":
        delta = event.get("delta")
        if isinstance(delta, str):
            deltas.append(delta)
    elif event_type == "transcript.text.done":
        done_text = str(event.get("text") or "")
        raw_usage = event.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else None
    elif event_type == "error":
        raise StepFunASRError(str(event.get("message") or "StepFun ASR returned an error"))
    return done_text, usage
