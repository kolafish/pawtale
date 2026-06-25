from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Callable
from typing import Protocol

from app.services.memoir.voice_tts import (
    DEFAULT_STREAM_TTS_SAMPLE_RATE,
    StreamingTTSChunk,
    TTSClient,
)


STREAM_TTS_RESPONSE_FORMAT = "pcm"
STREAM_TTS_SAMPLE_RATE = DEFAULT_STREAM_TTS_SAMPLE_RATE
STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME = "pcm_frame"
TTS_INPUT_CHUNK_MIN_CHARS = 12
TTS_INPUT_CHUNK_MAX_CHARS = 80
TTS_INPUT_CHUNK_MAX_WAIT_SECONDS = 0.25
_PCM_MIME_TYPES = {"audio/l16", "audio/pcm", "audio/x-pcm"}
_TTS_CHUNK_BOUNDARY_CHARS = set("。！？!?；;\n")


class StreamingTTSEventClient(Protocol):
    async def send_json(self, payload: dict) -> None:
        ...


async def stream_tts_events(
    *,
    client: StreamingTTSEventClient,
    tts_client: TTSClient,
    text_chunks: AsyncIterator[str],
    event_context: dict[str, object],
    voice: str | None,
    speed_ratio: float | None,
    cancel_event: asyncio.Event,
    audio_protocol: str = STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME,
    on_chunk: Callable[[StreamingTTSChunk], None] | None = None,
) -> None:
    normalize_audio_protocol(audio_protocol)
    await _stream_tts_frame_events(
        client=client,
        tts_client=tts_client,
        text_chunks=text_chunks,
        event_context=event_context,
        voice=voice,
        speed_ratio=speed_ratio,
        cancel_event=cancel_event,
        on_chunk=on_chunk,
    )


async def _stream_tts_frame_events(
    *,
    client: StreamingTTSEventClient,
    tts_client: TTSClient,
    text_chunks: AsyncIterator[str],
    event_context: dict[str, object],
    voice: str | None,
    speed_ratio: float | None,
    cancel_event: asyncio.Event,
    on_chunk: Callable[[StreamingTTSChunk], None] | None = None,
) -> None:
    if cancel_event.is_set():
        return
    sequence = 0
    frame_count = 0
    audio_bytes_seen = 0
    current_format: tuple[str, int | None, int, int] | None = None

    async for chunk in tts_client.stream_input_synthesize(
        text_chunks,
        voice=voice,
        speed_ratio=speed_ratio,
        response_format=STREAM_TTS_RESPONSE_FORMAT,
        sample_rate=STREAM_TTS_SAMPLE_RATE,
    ):
        if cancel_event.is_set():
            return
        if on_chunk is not None:
            on_chunk(chunk)
        if chunk.audio:
            sequence += 1
            frame_count += 1
            audio_bytes_seen += len(chunk.audio)
            next_format = (
                chunk.mime_type,
                chunk.sample_rate,
                chunk.channels,
                chunk.bits_per_sample,
            )
            if next_format != current_format:
                current_format = next_format
                await client.send_json(
                    {
                        "type": "audio.format",
                        **event_context,
                        "sequence": sequence,
                        "mime_type": chunk.mime_type,
                        "provider": chunk.provider,
                        "model": chunk.model,
                        "voice": chunk.voice,
                        "speed_ratio": chunk.speed_ratio,
                        "sample_rate": chunk.sample_rate,
                        "channels": chunk.channels,
                        "bits_per_sample": chunk.bits_per_sample,
                    }
                )
            await client.send_json(
                {
                    "type": "audio.frame",
                    **event_context,
                    "sequence": sequence,
                    "audio": base64.b64encode(chunk.audio).decode("ascii"),
                    "mime_type": chunk.mime_type,
                    "provider": chunk.provider,
                    "model": chunk.model,
                    "voice": chunk.voice,
                    "speed_ratio": chunk.speed_ratio,
                    "sample_rate": chunk.sample_rate,
                    "channels": chunk.channels,
                    "bits_per_sample": chunk.bits_per_sample,
                }
            )
        if chunk.is_final:
            duration_seconds = chunk.duration_seconds
            if duration_seconds is None:
                duration_seconds = _pcm_duration_seconds_from_size(
                    audio_bytes_seen,
                    mime_type=chunk.mime_type,
                    sample_rate=chunk.sample_rate,
                    channels=chunk.channels,
                    bits_per_sample=chunk.bits_per_sample,
                )
            await client.send_json(
                {
                    "type": "audio.done",
                    **event_context,
                    "frame_count": frame_count,
                    "duration_seconds": duration_seconds,
                    "provider": chunk.provider,
                    "model": chunk.model,
                    "voice": chunk.voice,
                    "speed_ratio": chunk.speed_ratio,
                    "sample_rate": chunk.sample_rate,
                    "channels": chunk.channels,
                    "bits_per_sample": chunk.bits_per_sample,
                }
            )


async def queued_text_chunks(
    text_queue: asyncio.Queue[str | None],
    cancel_event: asyncio.Event,
) -> AsyncIterator[str]:
    while not cancel_event.is_set():
        text = await text_queue.get()
        if text is None:
            return
        if text:
            yield text


async def chunk_text_for_tts(
    text_chunks: AsyncIterator[str],
    cancel_event: asyncio.Event,
    *,
    min_chars: int = TTS_INPUT_CHUNK_MIN_CHARS,
    max_chars: int = TTS_INPUT_CHUNK_MAX_CHARS,
    max_wait_seconds: float = TTS_INPUT_CHUNK_MAX_WAIT_SECONDS,
) -> AsyncIterator[str]:
    buffer = ""
    iterator = text_chunks.__aiter__()
    pending_text: asyncio.Task[str] | None = None
    try:
        while not cancel_event.is_set():
            if pending_text is None:
                pending_text = asyncio.create_task(iterator.__anext__())
            try:
                text = await asyncio.wait_for(
                    asyncio.shield(pending_text),
                    timeout=max(0.01, max_wait_seconds) if buffer else None,
                )
            except asyncio.TimeoutError:
                chunk = buffer.strip()
                buffer = ""
                if chunk:
                    yield chunk
                continue
            except StopAsyncIteration:
                pending_text = None
                break
            pending_text = None
            if not text:
                continue
            buffer += text
            while True:
                boundary = _tts_chunk_boundary(buffer, min_chars=min_chars, max_chars=max_chars)
                if boundary <= 0:
                    break
                chunk = buffer[:boundary].strip()
                buffer = buffer[boundary:]
                if chunk:
                    yield chunk
    finally:
        if pending_text is not None and not pending_text.done():
            pending_text.cancel()
    chunk = buffer.strip()
    if chunk and not cancel_event.is_set():
        yield chunk


async def single_text_chunk(text: str) -> AsyncIterator[str]:
    if text:
        yield text


def normalize_audio_protocol(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"pcm_frame", "audio_frame", "frame", "pcm"}:
        return STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME
    if not text:
        return STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME
    raise ValueError(f"unsupported streaming TTS audio protocol: {text}")


def _pcm_duration_seconds_from_size(
    byte_count: int,
    *,
    mime_type: str,
    sample_rate: int | None,
    channels: int,
    bits_per_sample: int,
) -> float | None:
    if byte_count <= 0:
        return None
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized not in _PCM_MIME_TYPES:
        return None
    rate = sample_rate or STREAM_TTS_SAMPLE_RATE
    bytes_per_second = rate * max(1, channels) * max(1, bits_per_sample) / 8
    if bytes_per_second <= 0:
        return None
    return byte_count / bytes_per_second


def _tts_chunk_boundary(text: str, *, min_chars: int, max_chars: int) -> int:
    if not text:
        return 0
    normalized_min = max(1, min_chars)
    normalized_max = max(normalized_min, max_chars)
    scan_limit = min(len(text), normalized_max)
    for index, char in enumerate(text[:scan_limit], start=1):
        if index >= normalized_min and char in _TTS_CHUNK_BOUNDARY_CHARS:
            return index
    if len(text) >= normalized_max:
        return normalized_max
    return 0
