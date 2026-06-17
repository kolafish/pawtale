from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

import websockets

from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.volc_asr_protocol import VolcASRProtocolCodec

ASR_PROVIDER_ERROR = "asr_provider_error"
ASR_PROVIDER_INVALID_FRAME = "asr_provider_invalid_frame"


@dataclass(frozen=True)
class VolcStreamingASRConfig:
    ws_url: str
    resource_id: str
    api_key: str = ""
    app_key: str = ""
    access_key: str = ""
    connect_timeout_seconds: float = 10.0

    def validate_credentials(self) -> None:
        if self.app_key and self.access_key:
            return
        if self.api_key:
            return
        raise RuntimeError("Volc streaming ASR credentials are not configured")


class VolcStreamingASRClient:
    def __init__(
        self,
        *,
        config: VolcStreamingASRConfig,
        user_id: str,
        voice_session_id: str,
        codec: VolcASRProtocolCodec | None = None,
    ) -> None:
        self.config = config
        self.user_id = user_id
        self.voice_session_id = voice_session_id
        self.codec = codec or VolcASRProtocolCodec()
        self.request_id = str(uuid.uuid4())
        self._connection: Any | None = None
        self._sequence = 1

    async def start(self) -> None:
        self.config.validate_credentials()
        try:
            self._connection = await websockets.connect(
                self.config.ws_url,
                additional_headers=self._headers(),
                open_timeout=self.config.connect_timeout_seconds,
                ping_interval=20,
                ping_timeout=20,
            )
        except Exception as exc:
            raise RuntimeError(_provider_connect_error_code(exc)) from exc
        await self._connection.send(
            self.codec.build_full_request(self._initial_request(), sequence=self._next_sequence())
        )

    async def send_audio(self, audio: bytes) -> None:
        await self._send(self.codec.build_audio_request(audio, sequence=self._next_sequence()))

    async def finish(self) -> None:
        await self._send(self.codec.build_audio_request(b"", is_last=True, sequence=self._next_sequence()))

    async def events(self) -> AsyncIterator[StreamingASREvent]:
        if self._connection is None:
            raise RuntimeError("Volc streaming ASR client has not been started")
        async for message in self._connection:
            try:
                if isinstance(message, bytes):
                    server_message = self.codec.parse_server_message(message)
                else:
                    server_message = self.codec.text_message_to_server_message(message)
                    if server_message is None:
                        continue
                event = self.codec.message_to_event(server_message)
            except Exception as exc:
                event = StreamingASREvent(
                    type="error",
                    error=_provider_event_error_code(exc),
                    raw=_provider_event_error_raw(exc, message),
                )
            if event:
                yield event

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _send(self, frame: bytes) -> None:
        if self._connection is None:
            raise RuntimeError("Volc streaming ASR client has not been started")
        await self._connection.send(frame)

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Api-Request-Id": self.request_id,
            "X-Api-Resource-Id": self.config.resource_id,
        }
        if self.config.app_key and self.config.access_key:
            headers["X-Api-App-Key"] = self.config.app_key
            headers["X-Api-Access-Key"] = self.config.access_key
        elif self.config.api_key:
            headers["X-Api-Key"] = self.config.api_key
        return headers

    def _initial_request(self) -> dict[str, Any]:
        return {
            "user": {"uid": self.user_id},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": True,
                "result_type": "full",
                "voice_session_id": self.voice_session_id,
            },
        }

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence


def _provider_connect_error_code(exc: Exception) -> str:
    status_code = _provider_response_status_code(exc)
    response_error = _provider_response_error_text(exc).lower()
    exception_text = str(exc).lower()
    combined = f"{response_error} {exception_text}"

    if "requested resource not granted" in combined:
        return "asr_provider_resource_not_granted"
    if "api key" in combined and ("invalid" in combined or "unauthorized" in combined):
        return "asr_provider_invalid_credentials"
    if status_code in {401, 403}:
        return "asr_provider_forbidden"
    if "timeout" in combined or "timed out" in combined:
        return "asr_provider_connection_timeout"
    return "asr_provider_connection_failed"


def _provider_event_error_code(exc: Exception) -> str:
    if isinstance(exc, ValueError) and str(exc).startswith("Volc ASR frame"):
        return ASR_PROVIDER_INVALID_FRAME
    return ASR_PROVIDER_ERROR


def _provider_event_error_raw(exc: Exception, message: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "provider": "volc_streaming_asr",
        "error_type": exc.__class__.__name__,
        "error": str(exc),
    }
    if isinstance(message, bytes):
        raw.update(_provider_binary_frame_metadata(message))
    elif isinstance(message, str):
        raw["text_frame_length"] = len(message)
    return raw


def _provider_binary_frame_metadata(message: bytes) -> dict[str, Any]:
    metadata: dict[str, Any] = {"frame_length": len(message)}
    if len(message) < 4:
        return metadata

    first, second, third, fourth = message[:4]
    metadata.update(
        {
            "frame_header_hex": bytes([first, second, third, fourth]).hex(),
            "header_size_bytes": (first & 0x0F) * 4,
            "message_type": second >> 4,
            "message_flags": second & 0x0F,
            "serialization": third >> 4,
            "compression": third & 0x0F,
            "reserved": fourth,
        }
    )
    return metadata


def _provider_response_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _provider_response_error_text(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    body = getattr(response, "body", b"")
    if isinstance(body, bytearray):
        body = bytes(body)
    if isinstance(body, bytes):
        raw = body.decode("utf-8", errors="replace")
    else:
        raw = str(body or "")
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        value = data.get("error") or data.get("message") or data.get("detail")
        return str(value or raw)
    return raw
