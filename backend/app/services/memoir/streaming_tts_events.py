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
STREAM_AUDIO_SEGMENT_MIN_BYTES = 16000
_PCM_MIME_TYPES = {"audio/l16", "audio/pcm", "audio/x-pcm"}


class StreamingTTSEventClient(Protocol):
    async def send_json(self, payload: dict) -> None:
        ...


async def stream_tts_events(
    *,
    client: StreamingTTSEventClient,
    tts_client: TTSClient,
    text_chunks: AsyncIterator[str],
    current_text: Callable[[], str],
    event_context: dict[str, object],
    voice: str | None,
    speed_ratio: float | None,
    cancel_event: asyncio.Event,
    on_chunk: Callable[[StreamingTTSChunk], None] | None = None,
) -> None:
    if cancel_event.is_set():
        return
    audio_buffer: list[bytes] = []
    audio_buffer_size = 0
    segment_index = 0
    sequence = 0
    text_cursor = 0

    async def flush_audio_segment(chunk: StreamingTTSChunk, *, final: bool) -> None:
        nonlocal audio_buffer, audio_buffer_size, segment_index, sequence, text_cursor
        if not audio_buffer:
            return
        audio = b"".join(audio_buffer)
        duration_seconds = _pcm_duration_seconds(
            audio,
            mime_type=chunk.mime_type,
            sample_rate=chunk.sample_rate,
            channels=chunk.channels,
            bits_per_sample=chunk.bits_per_sample,
        )
        segment_text, text_cursor = _next_audio_segment_text(
            current_text(),
            text_cursor,
            duration_seconds=duration_seconds,
            final=final,
        )
        await client.send_json(
            {
                "type": "text.segment",
                **event_context,
                "segment_index": segment_index,
                "text": segment_text,
            }
        )
        sequence += 1
        await client.send_json(
            {
                "type": "audio.chunk",
                **event_context,
                "segment_index": segment_index,
                "sequence": sequence,
                "audio": base64.b64encode(audio).decode("ascii"),
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
                "type": "audio.segment_done",
                **event_context,
                "segment_index": segment_index,
                "duration_seconds": duration_seconds,
            }
        )
        segment_index += 1
        audio_buffer = []
        audio_buffer_size = 0

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
            audio_buffer.append(chunk.audio)
            audio_buffer_size += len(chunk.audio)
            if audio_buffer_size >= STREAM_AUDIO_SEGMENT_MIN_BYTES:
                await flush_audio_segment(chunk, final=False)
        if chunk.is_final:
            await flush_audio_segment(chunk, final=True)


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


async def single_text_chunk(text: str) -> AsyncIterator[str]:
    if text:
        yield text


def _pcm_duration_seconds(
    audio: bytes,
    *,
    mime_type: str,
    sample_rate: int | None,
    channels: int,
    bits_per_sample: int,
) -> float | None:
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized not in _PCM_MIME_TYPES:
        return None
    rate = sample_rate or STREAM_TTS_SAMPLE_RATE
    bytes_per_second = rate * max(1, channels) * max(1, bits_per_sample) / 8
    if bytes_per_second <= 0:
        return None
    return len(audio) / bytes_per_second


def _next_audio_segment_text(
    text: str,
    cursor: int,
    *,
    duration_seconds: float | None,
    final: bool,
) -> tuple[str, int]:
    if cursor >= len(text):
        return "", cursor
    if final:
        return text[cursor:], len(text)
    available = text[cursor:]
    estimated_chars = max(1, int(round((duration_seconds or 0.5) * 6)))
    segment_text = available[:estimated_chars]
    return segment_text, cursor + len(segment_text)
