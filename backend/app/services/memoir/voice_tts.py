from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncIterator
import contextlib
from dataclasses import dataclass, field
import json
import logging
import math
import struct
from typing import Any, Protocol
import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import AudioFile, MemoirChatMessage, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirAssistantTTSRequest,
    MemoirAssistantTTSResponse,
)
from app.services.memoir.audio_files import delete_audio_file_paths, discard_staged_audio, stage_audio_bytes
from app.services.memoir.tts_alignment import (
    TTSAlignmentSegment,
    alignment_segments_from_json,
    alignment_segments_from_volc_words,
    alignment_segments_to_json,
)
from app.services.provider_limits import provider_concurrency_limiter
import websockets


logger = logging.getLogger(__name__)
TTS_UNAVAILABLE_MESSAGE = "语音合成服务暂时不可用，请稍后重试。"
SUPPORTED_RESPONSE_FORMATS = {
    "mp3": ("audio/mpeg", ".mp3", "mp3"),
    "wav": ("audio/wav", ".wav", "wav"),
    "ogg": ("audio/ogg", ".ogg", "ogg_opus"),
    "ogg_opus": ("audio/ogg", ".ogg", "ogg_opus"),
    "pcm": ("audio/L16", ".pcm", "pcm"),
}
TTS_ALIGNMENT_METADATA_KEY = "tts_alignment"
TTS_TIMING_SOURCE_METADATA_KEY = "tts_timing_source"
TTS_SPEED_RATIO_METADATA_KEY = "tts_speed_ratio"
DEFAULT_TTS_SPEED_RATIO = 1.0
DEFAULT_STREAM_TTS_RESPONSE_FORMAT = "pcm"
DEFAULT_STREAM_TTS_SAMPLE_RATE = 16000
DEFAULT_STREAM_TTS_CHANNELS = 1
DEFAULT_STREAM_TTS_BITS_PER_SAMPLE = 16

_VOLC_BIDI_MSG_FULL_CLIENT_REQUEST = 0x14
_VOLC_BIDI_MSG_FULL_SERVER_RESPONSE = 0x94
_VOLC_BIDI_MSG_AUDIO_ONLY_RESPONSE = 0xB4
_VOLC_BIDI_MSG_ERROR = 0xF0
_VOLC_BIDI_SERIALIZATION_JSON = 0x10
_VOLC_BIDI_START_CONNECTION = 1
_VOLC_BIDI_FINISH_CONNECTION = 2
_VOLC_BIDI_CONNECTION_STARTED = 50
_VOLC_BIDI_CONNECTION_FAILED = 51
_VOLC_BIDI_START_SESSION = 100
_VOLC_BIDI_FINISH_SESSION = 102
_VOLC_BIDI_SESSION_STARTED = 150
_VOLC_BIDI_SESSION_FINISHED = 152
_VOLC_BIDI_SESSION_FAILED = 153
_VOLC_BIDI_TASK_REQUEST = 200
_VOLC_BIDI_TTS_RESPONSE = 352


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime_type: str
    extension: str
    provider: str
    model: str
    voice: str
    speed_ratio: float = DEFAULT_TTS_SPEED_RATIO
    alignment: list[TTSAlignmentSegment] = field(default_factory=list)
    duration_seconds: float | None = None


@dataclass(frozen=True)
class StreamingTTSChunk:
    audio: bytes
    mime_type: str
    provider: str
    model: str
    voice: str
    speed_ratio: float = DEFAULT_TTS_SPEED_RATIO
    sequence: int = 0
    alignment: list[TTSAlignmentSegment] = field(default_factory=list)
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int = DEFAULT_STREAM_TTS_CHANNELS
    bits_per_sample: int = DEFAULT_STREAM_TTS_BITS_PER_SAMPLE
    is_final: bool = False
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VolcTTSResponseFormat:
    mime_type: str
    extension: str
    provider_value: str


class TTSClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        ...

    @property
    def default_voice(self) -> str:
        ...

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
    ) -> TTSResult:
        ...

    def stream_synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
    ) -> AsyncIterator[StreamingTTSChunk]:
        raise NotImplementedError(f"{self.provider_name} does not support streaming TTS")

    def stream_input_synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
        response_format: str | None = None,
        sample_rate: int | None = None,
    ) -> AsyncIterator[StreamingTTSChunk]:
        raise NotImplementedError(f"{self.provider_name} does not support bidirectional streaming TTS")


class VolcTTSClient:
    provider_name = "volc_tts"

    def __init__(self, settings: Settings):
        self.app_id = settings.volc_tts_app_id.strip()
        self.access_token = settings.volc_tts_access_token.strip()
        self.api_key = settings.volc_tts_api_key.strip()
        self.endpoint = settings.volc_tts_endpoint.strip()
        self.bidirectional_endpoint = settings.volc_tts_bidirectional_endpoint.strip()
        self.resource_id = settings.volc_tts_resource_id.strip()
        self.default_voice = settings.volc_tts_speaker.strip()
        self.response_format = _volc_response_format(settings.volc_tts_response_format)
        self.sample_rate = settings.volc_tts_sample_rate
        self.timeout_seconds = settings.tts_timeout_seconds
        self.max_concurrent_requests = settings.tts_max_concurrent_requests
        self.model_name = self.resource_id

    def effective_voice(self, voice: str | None = None) -> str:
        return _volc_effective_voice(voice, self.default_voice)

    def _request_context(
        self,
        text: str,
        *,
        voice: str | None,
        speed_ratio: float | None,
    ) -> tuple[str, float, dict[str, object], dict[str, str], float]:
        if not self.app_id or not self.access_token:
            raise HTTPException(status_code=503, detail="火山 TTS app id 或 access token 未配置")
        if not self.endpoint:
            raise HTTPException(status_code=503, detail="火山 TTS endpoint 未配置")
        if not self.resource_id:
            raise HTTPException(status_code=503, detail="火山 TTS resource id 未配置")
        effective_voice = self.effective_voice(voice)
        if not effective_voice:
            raise HTTPException(status_code=503, detail="火山 TTS 音色未配置")
        effective_speed_ratio = _effective_tts_speed_ratio(speed_ratio)

        request_id = str(uuid.uuid4())
        payload = _volc_sse_payload(
            text=text,
            voice=effective_voice,
            response_format=self.response_format,
            sample_rate=self.sample_rate,
            speed_ratio=effective_speed_ratio,
        )
        headers = {
            "Content-Type": "application/json",
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": request_id,
        }
        timeout = max(1.0, float(self.timeout_seconds))
        return effective_voice, effective_speed_ratio, payload, headers, timeout

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
    ) -> TTSResult:
        (
            effective_voice,
            effective_speed_ratio,
            payload,
            headers,
            timeout,
        ) = self._request_context(
            text,
            voice=voice,
            speed_ratio=speed_ratio,
        )

        async def request() -> httpx.Response:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                return response

        try:
            response = await provider_concurrency_limiter.run(
                "tts",
                self.max_concurrent_requests,
                request,
            )
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "Volc TTS rejected request status=%s logid=%s",
                exc.response.status_code,
                exc.response.headers.get("x-tt-logid"),
            )
            raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc
        except httpx.HTTPError as exc:
            logger.exception("Volc TTS request failed")
            raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc

        audio, alignment = _parse_volc_sse_response(text, response.text)
        if not audio:
            raise HTTPException(status_code=502, detail="TTS 未返回可用音频")
        duration_seconds = max((segment.end_ms for segment in alignment), default=0) / 1000 if alignment else None
        return TTSResult(
            audio=audio,
            mime_type=self.response_format.mime_type,
            extension=self.response_format.extension,
            provider=self.provider_name,
            model=self.model_name,
            voice=effective_voice,
            speed_ratio=effective_speed_ratio,
            alignment=alignment,
            duration_seconds=duration_seconds,
        )

    async def stream_synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
    ) -> AsyncIterator[StreamingTTSChunk]:
        (
            effective_voice,
            effective_speed_ratio,
            payload,
            headers,
            timeout,
        ) = self._request_context(
            text,
            voice=voice,
            speed_ratio=speed_ratio,
        )
        audio_seen = False
        words: list[object] = []
        sequence = 0

        try:
            async with provider_concurrency_limiter.slot("tts", self.max_concurrent_requests):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST",
                        self.endpoint,
                        json=payload,
                        headers=headers,
                    ) as response:
                        response.raise_for_status()
                        async for payload_item in _iter_volc_sse_payloads(response.aiter_lines()):
                            code = payload_item.get("code")
                            if isinstance(code, int) and code not in {0, 20000000}:
                                if not audio_seen:
                                    raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE)
                                logger.warning(
                                    "Volc streaming TTS returned an error after audio chunks: %s",
                                    payload_item,
                                )

                            audio = payload_item.get("data")
                            if isinstance(audio, str) and audio:
                                try:
                                    decoded = base64.b64decode(audio)
                                except (binascii.Error, ValueError):
                                    logger.warning("Volc streaming TTS returned an invalid audio chunk")
                                    continue
                                if decoded:
                                    audio_seen = True
                                    sequence += 1
                                    yield StreamingTTSChunk(
                                        audio=decoded,
                                        mime_type=self.response_format.mime_type,
                                        provider=self.provider_name,
                                        model=self.model_name,
                                        voice=effective_voice,
                                        speed_ratio=effective_speed_ratio,
                                        sequence=sequence,
                                        raw=payload_item,
                                    )

                            sentence = payload_item.get("sentence")
                            if isinstance(sentence, dict):
                                sentence_words = sentence.get("words")
                                if isinstance(sentence_words, list):
                                    words.extend(sentence_words)
        except HTTPException:
            raise
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "Volc streaming TTS rejected request status=%s logid=%s",
                exc.response.status_code,
                exc.response.headers.get("x-tt-logid"),
            )
            raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc
        except httpx.HTTPError as exc:
            logger.exception("Volc streaming TTS request failed")
            raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc

        if not audio_seen:
            raise HTTPException(status_code=502, detail="TTS 未返回可用音频")
        alignment = alignment_segments_from_volc_words(text, words)
        yield StreamingTTSChunk(
            audio=b"",
            mime_type=self.response_format.mime_type,
            provider=self.provider_name,
            model=self.model_name,
            voice=effective_voice,
            speed_ratio=effective_speed_ratio,
            sequence=sequence + 1,
            alignment=alignment,
            duration_seconds=max((segment.end_ms for segment in alignment), default=0) / 1000 if alignment else None,
            is_final=True,
        )

    async def stream_input_synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
        response_format: str | None = None,
        sample_rate: int | None = None,
    ) -> AsyncIterator[StreamingTTSChunk]:
        effective_voice, effective_speed_ratio, output_format, output_sample_rate, headers, timeout = (
            self._bidirectional_context(
                voice=voice,
                speed_ratio=speed_ratio,
                response_format=response_format,
                sample_rate=sample_rate,
            )
        )
        connection: Any | None = None
        producer_task: asyncio.Task[None] | None = None
        audio_seen = False
        sequence = 0
        audio_bytes_seen = 0
        session_id = str(uuid.uuid4())

        try:
            async with provider_concurrency_limiter.slot("tts", self.max_concurrent_requests):
                connection = await websockets.connect(
                    self.bidirectional_endpoint,
                    additional_headers=headers,
                    open_timeout=timeout,
                    ping_interval=20,
                    ping_timeout=20,
                )
                await _volc_bidi_send_packet(
                    connection,
                    _VOLC_BIDI_START_CONNECTION,
                    {"event": _VOLC_BIDI_START_CONNECTION},
                )
                connection_started = _volc_bidi_parse_packet(await connection.recv())
                if connection_started.event != _VOLC_BIDI_CONNECTION_STARTED:
                    raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE)

                await _volc_bidi_send_packet(
                    connection,
                    _VOLC_BIDI_START_SESSION,
                    _volc_bidi_start_session_payload(
                        voice=effective_voice,
                        response_format=output_format,
                        sample_rate=output_sample_rate,
                        speed_ratio=effective_speed_ratio,
                    ),
                    session_id=session_id,
                )
                session_started = _volc_bidi_parse_packet(await connection.recv())
                if session_started.event != _VOLC_BIDI_SESSION_STARTED:
                    raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE)

                producer_task = asyncio.create_task(
                    _volc_bidi_send_text_chunks(connection, text_chunks, session_id=session_id)
                )
                while True:
                    packet = _volc_bidi_parse_packet(await connection.recv())
                    if packet.error_code is not None:
                        raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE)
                    if packet.event in {_VOLC_BIDI_CONNECTION_FAILED, _VOLC_BIDI_SESSION_FAILED}:
                        raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE)
                    if packet.event == _VOLC_BIDI_TTS_RESPONSE and packet.audio:
                        audio_seen = True
                        sequence += 1
                        audio_bytes_seen += len(packet.audio)
                        yield StreamingTTSChunk(
                            audio=packet.audio,
                            mime_type=output_format.mime_type,
                            provider=self.provider_name,
                            model=self.model_name,
                            voice=effective_voice,
                            speed_ratio=effective_speed_ratio,
                            sequence=sequence,
                            sample_rate=output_sample_rate,
                            channels=DEFAULT_STREAM_TTS_CHANNELS,
                            bits_per_sample=DEFAULT_STREAM_TTS_BITS_PER_SAMPLE,
                            raw=packet.payload,
                        )
                    if packet.event == _VOLC_BIDI_SESSION_FINISHED:
                        break

                if producer_task is not None:
                    await producer_task
                if not audio_seen:
                    raise HTTPException(status_code=502, detail="TTS 未返回可用音频")
                yield StreamingTTSChunk(
                    audio=b"",
                    mime_type=output_format.mime_type,
                    provider=self.provider_name,
                    model=self.model_name,
                    voice=effective_voice,
                    speed_ratio=effective_speed_ratio,
                    sequence=sequence + 1,
                    duration_seconds=_stream_audio_duration_seconds(
                        audio_bytes_seen,
                        output_format.mime_type,
                        output_sample_rate,
                    ),
                    sample_rate=output_sample_rate,
                    channels=DEFAULT_STREAM_TTS_CHANNELS,
                    bits_per_sample=DEFAULT_STREAM_TTS_BITS_PER_SAMPLE,
                    is_final=True,
                )
                await _volc_bidi_send_packet(
                    connection,
                    _VOLC_BIDI_FINISH_CONNECTION,
                    {"event": _VOLC_BIDI_FINISH_CONNECTION},
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Volc bidirectional TTS request failed")
            raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE) from exc
        finally:
            if producer_task is not None and not producer_task.done():
                producer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer_task
            if connection is not None:
                with contextlib.suppress(Exception):
                    await connection.close()

    def _bidirectional_context(
        self,
        *,
        voice: str | None,
        speed_ratio: float | None,
        response_format: str | None,
        sample_rate: int | None,
    ) -> tuple[str, float, VolcTTSResponseFormat, int, dict[str, str], float]:
        if not ((self.app_id and self.access_token) or self.api_key):
            raise HTTPException(status_code=503, detail="火山 TTS 凭证未配置")
        if not self.bidirectional_endpoint:
            raise HTTPException(status_code=503, detail="火山双向 TTS endpoint 未配置")
        if not self.resource_id:
            raise HTTPException(status_code=503, detail="火山 TTS resource id 未配置")
        effective_voice = self.effective_voice(voice)
        if not effective_voice:
            raise HTTPException(status_code=503, detail="火山 TTS 音色未配置")
        effective_speed_ratio = _effective_tts_speed_ratio(speed_ratio)
        output_format = _volc_response_format(response_format or DEFAULT_STREAM_TTS_RESPONSE_FORMAT)
        output_sample_rate = _effective_stream_sample_rate(sample_rate)
        headers = {
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Control-Require-Usage-Tokens-Return": "*",
        }
        if self.app_id and self.access_token:
            headers["X-Api-App-Key"] = self.app_id
            headers["X-Api-Access-Key"] = self.access_token
        else:
            headers["X-Api-Key"] = self.api_key
        return (
            effective_voice,
            effective_speed_ratio,
            output_format,
            output_sample_rate,
            headers,
            max(1.0, float(self.timeout_seconds)),
        )


@dataclass(frozen=True)
class _VolcBidirectionalPacket:
    message_type: int
    event: int | None = None
    connection_id: str = ""
    session_id: str = ""
    audio: bytes = b""
    payload: dict[str, object] = field(default_factory=dict)
    error_code: int | None = None
    error_message: str = ""


def _volc_bidi_start_session_payload(
    *,
    voice: str,
    response_format: VolcTTSResponseFormat,
    sample_rate: int,
    speed_ratio: float,
) -> dict[str, object]:
    audio_params: dict[str, object] = {
        "format": response_format.provider_value,
        "sample_rate": sample_rate,
        "enable_subtitle": False,
    }
    speech_rate = _volc_speech_rate_from_speed_ratio(speed_ratio)
    if speech_rate is not None:
        audio_params["speech_rate"] = speech_rate
    return {
        "event": _VOLC_BIDI_START_SESSION,
        "namespace": "BidirectionalTTS",
        "user": {"uid": "pawtale-backend"},
        "req_params": {
            "speaker": voice,
            "audio_params": audio_params,
            "additions": json.dumps({"disable_markdown_filter": True}, separators=(",", ":")),
        },
    }


async def _volc_bidi_send_text_chunks(
    connection: Any,
    text_chunks: AsyncIterator[str],
    *,
    session_id: str,
) -> None:
    async for text in text_chunks:
        if not text:
            continue
        await _volc_bidi_send_packet(
            connection,
            _VOLC_BIDI_TASK_REQUEST,
            {
                "event": _VOLC_BIDI_TASK_REQUEST,
                "namespace": "BidirectionalTTS",
                "req_params": {"text": text},
            },
            session_id=session_id,
        )
    await _volc_bidi_send_packet(
        connection,
        _VOLC_BIDI_FINISH_SESSION,
        {
            "event": _VOLC_BIDI_FINISH_SESSION,
            "namespace": "BidirectionalTTS",
        },
        session_id=session_id,
    )


async def _volc_bidi_send_packet(
    connection: Any,
    event: int,
    payload: dict[str, object],
    *,
    session_id: str = "",
) -> None:
    await connection.send(_volc_bidi_client_packet(event, payload, session_id=session_id))


def _volc_bidi_client_packet(
    event: int,
    payload: dict[str, object],
    *,
    session_id: str = "",
) -> bytes:
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    parts = [
        struct.pack(
            ">BBBBi",
            0x11,
            _VOLC_BIDI_MSG_FULL_CLIENT_REQUEST,
            _VOLC_BIDI_SERIALIZATION_JSON,
            0x00,
            event,
        )
    ]
    if session_id:
        session_bytes = session_id.encode("utf-8")
        parts.append(struct.pack(">I", len(session_bytes)))
        parts.append(session_bytes)
    parts.append(struct.pack(">I", len(payload_bytes)))
    parts.append(payload_bytes)
    return b"".join(parts)


def _volc_bidi_parse_packet(message: bytes | str) -> _VolcBidirectionalPacket:
    if isinstance(message, str):
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid Volc bidirectional TTS text packet") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid Volc bidirectional TTS text payload")
        event = payload.get("event")
        return _VolcBidirectionalPacket(
            message_type=_VOLC_BIDI_MSG_FULL_SERVER_RESPONSE,
            event=event if isinstance(event, int) else None,
            payload=payload,
        )

    if len(message) < 8:
        raise ValueError("invalid Volc bidirectional TTS packet")
    message_type = message[1]
    offset = 4
    if message_type == _VOLC_BIDI_MSG_ERROR:
        error_code, offset = _read_i32(message, offset)
        raw_payload, offset = _read_length_prefixed_bytes(message, offset)
        payload = _json_payload(raw_payload)
        error_message = str(payload.get("message") or payload.get("error") or error_code)
        return _VolcBidirectionalPacket(
            message_type=message_type,
            payload=payload,
            error_code=error_code,
            error_message=error_message,
        )

    event, offset = _read_i32(message, offset)
    if event in {_VOLC_BIDI_CONNECTION_STARTED, _VOLC_BIDI_CONNECTION_FAILED}:
        raw_connection_id, offset = _read_length_prefixed_bytes(message, offset)
        raw_payload, offset = _read_length_prefixed_bytes(message, offset)
        return _VolcBidirectionalPacket(
            message_type=message_type,
            event=event,
            connection_id=raw_connection_id.decode("utf-8", errors="replace"),
            payload=_json_payload(raw_payload),
        )

    raw_session_id, offset = _read_length_prefixed_bytes(message, offset)
    if message_type == _VOLC_BIDI_MSG_AUDIO_ONLY_RESPONSE:
        audio, offset = _read_length_prefixed_bytes(message, offset)
        return _VolcBidirectionalPacket(
            message_type=message_type,
            event=event,
            session_id=raw_session_id.decode("utf-8", errors="replace"),
            audio=audio,
        )

    raw_payload, offset = _read_length_prefixed_bytes(message, offset)
    return _VolcBidirectionalPacket(
        message_type=message_type,
        event=event,
        session_id=raw_session_id.decode("utf-8", errors="replace"),
        payload=_json_payload(raw_payload),
    )


def _read_i32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ValueError("invalid Volc bidirectional TTS integer")
    return struct.unpack(">i", data[offset : offset + 4])[0], offset + 4


def _read_u32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ValueError("invalid Volc bidirectional TTS length")
    return struct.unpack(">I", data[offset : offset + 4])[0], offset + 4


def _read_length_prefixed_bytes(data: bytes, offset: int) -> tuple[bytes, int]:
    size, offset = _read_u32(data, offset)
    if offset + size > len(data):
        raise ValueError("invalid Volc bidirectional TTS payload")
    return data[offset : offset + size], offset + size


def _json_payload(raw_payload: bytes) -> dict[str, object]:
    if not raw_payload:
        return {}
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Volc bidirectional TTS returned an invalid JSON payload")
        return {}
    return payload if isinstance(payload, dict) else {}


def _volc_response_format(value: str) -> VolcTTSResponseFormat:
    normalized = (value or "mp3").strip().lower()
    if normalized not in SUPPORTED_RESPONSE_FORMATS:
        raise HTTPException(status_code=500, detail=f"不支持的火山 TTS 音频格式: {normalized}")
    mime_type, extension, provider_value = SUPPORTED_RESPONSE_FORMATS[normalized]
    return VolcTTSResponseFormat(mime_type=mime_type, extension=extension, provider_value=provider_value)


def _volc_sse_payload(
    *,
    text: str,
    voice: str,
    response_format: VolcTTSResponseFormat,
    sample_rate: int,
    speed_ratio: float,
) -> dict[str, object]:
    audio_params: dict[str, object] = {
        "format": response_format.provider_value,
        "sample_rate": sample_rate,
        "enable_subtitle": True,
    }
    speech_rate = _volc_speech_rate_from_speed_ratio(speed_ratio)
    if speech_rate is not None:
        audio_params["speech_rate"] = speech_rate
    return {
        "user": {"uid": "pawtale-backend"},
        "req_params": {
            "text": text,
            "speaker": voice,
            "audio_params": audio_params,
            "additions": json.dumps({"disable_markdown_filter": True}, separators=(",", ":")),
        },
    }


def _parse_volc_sse_response(text: str, response_text: str) -> tuple[bytes, list[TTSAlignmentSegment]]:
    audio_chunks: list[bytes] = []
    words: list[object] = []
    error_messages: list[str] = []
    for payload in _volc_sse_payloads(response_text):
        code = payload.get("code")
        if isinstance(code, int) and code not in {0, 20000000}:
            error_messages.append(str(payload.get("message") or f"火山 TTS 错误 {code}"))

        audio = payload.get("data")
        if isinstance(audio, str) and audio:
            try:
                audio_chunks.append(base64.b64decode(audio))
            except (binascii.Error, ValueError):
                logger.warning("Volc TTS returned an invalid audio chunk")

        sentence = payload.get("sentence")
        if isinstance(sentence, dict):
            sentence_words = sentence.get("words")
            if isinstance(sentence_words, list):
                words.extend(sentence_words)

    if error_messages and not audio_chunks:
        raise HTTPException(status_code=503, detail=TTS_UNAVAILABLE_MESSAGE)
    return b"".join(audio_chunks), alignment_segments_from_volc_words(text, words)


def _volc_sse_payloads(response_text: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in response_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        raw_payload = stripped.removeprefix("data:").strip()
        if not raw_payload or raw_payload == "[DONE]":
            continue
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning("Volc TTS returned an invalid SSE payload")
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


async def _iter_volc_sse_payloads(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, object]]:
    data_lines: list[str] = []
    async for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            payload = _parse_volc_sse_data_lines(data_lines)
            data_lines = []
            if payload is not None:
                yield payload
            continue
        if stripped.startswith("data:"):
            data_lines.append(stripped.removeprefix("data:").strip())

    payload = _parse_volc_sse_data_lines(data_lines)
    if payload is not None:
        yield payload


def _parse_volc_sse_data_lines(data_lines: list[str]) -> dict[str, object] | None:
    if not data_lines:
        return None
    raw_payload = "\n".join(data_lines).strip()
    if not raw_payload or raw_payload == "[DONE]":
        return None
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        logger.warning("Volc TTS returned an invalid SSE payload")
        return None
    return payload if isinstance(payload, dict) else None


def get_tts_client(settings: Settings | None = None) -> TTSClient:
    current_settings = settings or get_settings()
    provider = current_settings.tts_provider.strip().lower()
    if provider in {"volc", "volc_tts", "doubao"}:
        return VolcTTSClient(current_settings)
    raise HTTPException(status_code=503, detail="TTS_PROVIDER 未启用")


def _volc_effective_voice(requested_voice: str | None, default_voice: str) -> str:
    candidate = (requested_voice or "").strip()
    if _looks_like_volc_v3_voice(candidate):
        return candidate
    return default_voice.strip()


def _looks_like_volc_v3_voice(voice: str) -> bool:
    if not voice:
        return False
    return (
        voice.startswith("S_")
        or voice.endswith("_bigtts")
        or voice.endswith("_tob")
        or voice.startswith("ICL_")
        or voice.startswith("multi_")
    )


def _effective_tts_speed_ratio(speed_ratio: object) -> float:
    if speed_ratio is None:
        return DEFAULT_TTS_SPEED_RATIO
    try:
        value = float(speed_ratio)
    except (TypeError, ValueError):
        return DEFAULT_TTS_SPEED_RATIO
    if not math.isfinite(value):
        return DEFAULT_TTS_SPEED_RATIO
    return max(0.5, min(2.0, round(value, 2)))


def _effective_stream_sample_rate(sample_rate: object) -> int:
    if sample_rate is None:
        return DEFAULT_STREAM_TTS_SAMPLE_RATE
    try:
        value = int(sample_rate)
    except (TypeError, ValueError):
        return DEFAULT_STREAM_TTS_SAMPLE_RATE
    if value <= 0:
        return DEFAULT_STREAM_TTS_SAMPLE_RATE
    return value


def _stream_audio_duration_seconds(audio_bytes: int, mime_type: str, sample_rate: int) -> float | None:
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized not in {"audio/l16", "audio/pcm", "audio/x-pcm"}:
        return None
    bytes_per_second = sample_rate * DEFAULT_STREAM_TTS_CHANNELS * DEFAULT_STREAM_TTS_BITS_PER_SAMPLE / 8
    if bytes_per_second <= 0:
        return None
    return audio_bytes / bytes_per_second


def _volc_speech_rate_from_speed_ratio(speed_ratio: float) -> int | None:
    effective_speed_ratio = _effective_tts_speed_ratio(speed_ratio)
    if effective_speed_ratio == DEFAULT_TTS_SPEED_RATIO:
        return None
    return max(-50, min(100, int(round((effective_speed_ratio - 1.0) * 100))))


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
        raise HTTPException(status_code=400, detail="只有 AI 回应可以生成语音")
    if not message.content_text.strip():
        raise HTTPException(status_code=400, detail="AI 回应内容为空")
    return message


async def _load_existing_audio(db: AsyncSession, message: MemoirChatMessage) -> AudioFile | None:
    if not message.audio_file_id:
        return None
    result = await db.execute(select(AudioFile).where(AudioFile.id == message.audio_file_id))
    return result.scalar_one_or_none()


async def _audio_file_is_referenced(db: AsyncSession, audio_file_id: str) -> bool:
    queries = [
        select(MemoirChatMessage.id).where(MemoirChatMessage.audio_file_id == audio_file_id).limit(1),
        select(MemoirVoiceUtterance.id).where(MemoirVoiceUtterance.audio_file_id == audio_file_id).limit(1),
    ]
    for query in queries:
        if await db.scalar(query):
            return True
    return False


async def _delete_unreferenced_audio_file(db: AsyncSession, audio_file: AudioFile | None) -> None:
    if audio_file is None:
        return
    if await _audio_file_is_referenced(db, audio_file.id):
        return
    await db.delete(audio_file)
    await db.commit()
    try:
        await delete_audio_file_paths([audio_file])
    except Exception:
        logger.warning(
            "Failed to delete replaced TTS audio file audio_file_id=%s",
            audio_file.id,
            exc_info=True,
        )


def _message_alignment(message: MemoirChatMessage) -> list[dict[str, object]]:
    metadata = message.metadata_json or {}
    return alignment_segments_to_json(alignment_segments_from_json(metadata.get(TTS_ALIGNMENT_METADATA_KEY)))


def _message_has_alignment(message: MemoirChatMessage) -> bool:
    metadata = message.metadata_json or {}
    return bool(alignment_segments_from_json(metadata.get(TTS_ALIGNMENT_METADATA_KEY)))


def _effective_tts_voice(client: TTSClient, requested_voice: str | None) -> str:
    resolver = getattr(client, "effective_voice", None)
    if callable(resolver):
        return str(resolver(requested_voice) or "").strip()
    return (requested_voice or getattr(client, "default_voice", "") or "").strip()


def _can_reuse_existing_audio(
    message: MemoirChatMessage,
    client: TTSClient,
    *,
    voice: str,
    speed_ratio: float,
) -> bool:
    metadata = message.metadata_json or {}
    if metadata.get("tts_provider") != client.provider_name:
        return False
    if metadata.get("tts_model") != client.model_name:
        return False
    if str(metadata.get("tts_voice") or "") != voice:
        return False
    if _effective_tts_speed_ratio(metadata.get(TTS_SPEED_RATIO_METADATA_KEY)) != speed_ratio:
        return False
    return TTS_TIMING_SOURCE_METADATA_KEY in metadata or _message_has_alignment(message)


def _tts_timing_metadata(result: TTSResult) -> dict[str, object]:
    return {
        TTS_ALIGNMENT_METADATA_KEY: alignment_segments_to_json(result.alignment),
        TTS_TIMING_SOURCE_METADATA_KEY: result.provider if result.alignment else None,
        "tts_duration_seconds": result.duration_seconds,
        TTS_SPEED_RATIO_METADATA_KEY: result.speed_ratio,
    }


def _message_duration_seconds(message: MemoirChatMessage, audio_file: AudioFile | None = None) -> float | None:
    value = (message.metadata_json or {}).get("tts_duration_seconds")
    try:
        duration = float(value)
    except (TypeError, ValueError):
        duration = None
    if duration is not None and duration >= 0:
        return duration
    if audio_file and audio_file.duration_seconds is not None:
        return float(audio_file.duration_seconds)
    return None


def _tts_response(
    message: MemoirChatMessage,
    audio_file: AudioFile,
    *,
    provider: str,
    model: str,
) -> MemoirAssistantTTSResponse:
    metadata = message.metadata_json or {}
    return MemoirAssistantTTSResponse(
        message=message,
        audio_file_id=audio_file.id,
        provider=provider,
        model=model,
        mime_type=audio_file.mime_type,
        duration_seconds=_message_duration_seconds(message, audio_file),
        alignment_provider=str(metadata.get(TTS_TIMING_SOURCE_METADATA_KEY) or "") or None,
        alignment=_message_alignment(message),
    )


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
    client = tts_client or get_tts_client()
    effective_voice = _effective_tts_voice(client, req.voice)
    effective_speed_ratio = _effective_tts_speed_ratio(req.speed_ratio)
    if (
        existing_audio
        and not req.force_regenerate
        and _can_reuse_existing_audio(message, client, voice=effective_voice, speed_ratio=effective_speed_ratio)
    ):
        return _tts_response(
            message,
            existing_audio,
            provider=str(message.metadata_json.get("tts_provider") or "stored"),
            model=str(message.metadata_json.get("tts_model") or ""),
        )

    text = message.content_text.strip()
    try:
        result = await client.synthesize(text, voice=req.voice, speed_ratio=req.speed_ratio)
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
        duration_seconds=math.ceil(result.duration_seconds) if result.duration_seconds is not None else None,
    )
    try:
        message.audio_file_id = staged_audio.audio_file.id
        message.metadata_json = {
            **(message.metadata_json or {}),
            "tts_provider": result.provider,
            "tts_model": result.model,
            "tts_voice": result.voice,
            "tts_mime_type": result.mime_type,
            **_tts_timing_metadata(result),
        }
        db.add(message)
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio(staged_audio)
        raise

    await db.refresh(message)
    await db.refresh(staged_audio.audio_file)
    if existing_audio and existing_audio.id != staged_audio.audio_file.id:
        try:
            await _delete_unreferenced_audio_file(db, existing_audio)
        except Exception:
            await db.rollback()
            logger.warning(
                "Failed to delete replaced TTS audio row audio_file_id=%s",
                existing_audio.id,
                exc_info=True,
            )
    return _tts_response(
        message,
        staged_audio.audio_file,
        provider=result.provider,
        model=result.model,
    )
