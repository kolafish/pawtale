from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncIterator
import contextlib
from dataclasses import dataclass, field
import json
import logging
import struct
from typing import Any
import uuid

import httpx
from fastapi import HTTPException

from app.config import Settings, get_settings
from app.services.memoir.tts_alignment import (
    TTSAlignmentSegment,
    alignment_segments_from_volc_words,
)
from app.services.memoir.voice_tts_codec import (
    VolcTTSResponseFormat,
    effective_stream_sample_rate as _effective_stream_sample_rate,
    effective_tts_speed_ratio as _effective_tts_speed_ratio,
    stream_audio_duration_seconds as _stream_audio_duration_seconds,
    volc_effective_voice as _volc_effective_voice,
    volc_response_format as _volc_response_format,
    volc_speech_rate_from_speed_ratio as _volc_speech_rate_from_speed_ratio,
)
from app.services.memoir.voice_tts_types import (
    DEFAULT_STREAM_TTS_BITS_PER_SAMPLE,
    DEFAULT_STREAM_TTS_CHANNELS,
    DEFAULT_STREAM_TTS_RESPONSE_FORMAT,
    TTSClient,
    TTSResult,
    StreamingTTSChunk,
    TTS_UNAVAILABLE_MESSAGE,
)
from app.services.provider_limits import provider_concurrency_limiter
import websockets


logger = logging.getLogger(__name__)
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
