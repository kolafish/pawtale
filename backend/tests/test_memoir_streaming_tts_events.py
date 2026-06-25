import asyncio
import base64

import pytest

from app.services.memoir.streaming_tts_events import (
    STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME,
    chunk_text_for_tts,
    stream_tts_events,
)
from app.services.memoir.voice_tts import StreamingTTSChunk


@pytest.mark.asyncio
async def test_chunk_text_for_tts_buffers_small_llm_deltas_until_boundary():
    cancel_event = asyncio.Event()

    async def source():
        yield "一二三四五"
        yield "六七。八九"
        yield "十"

    chunks = [
        chunk
        async for chunk in chunk_text_for_tts(
            source(),
            cancel_event,
            min_chars=6,
            max_chars=20,
        )
    ]

    assert chunks == ["一二三四五六七。", "八九十"]


@pytest.mark.asyncio
async def test_chunk_text_for_tts_splits_long_text_without_punctuation():
    cancel_event = asyncio.Event()

    async def source():
        yield "abcdefghij"

    chunks = [
        chunk
        async for chunk in chunk_text_for_tts(
            source(),
            cancel_event,
            min_chars=4,
            max_chars=4,
        )
    ]

    assert chunks == ["abcd", "efgh", "ij"]


@pytest.mark.asyncio
async def test_chunk_text_for_tts_flushes_short_buffer_after_wait():
    cancel_event = asyncio.Event()

    async def source():
        yield "短半句"
        await asyncio.sleep(0.03)
        yield "后续。"

    chunks = [
        chunk
        async for chunk in chunk_text_for_tts(
            source(),
            cancel_event,
            min_chars=12,
            max_chars=80,
            max_wait_seconds=0.01,
        )
    ]

    assert chunks == ["短半句", "后续。"]


@pytest.mark.asyncio
async def test_stream_tts_events_can_emit_pcm_audio_frames():
    client = FakeEventClient()
    tts_client = FakeFrameTTS()
    observed_chunks = []
    cancel_event = asyncio.Event()

    async def text_chunks():
        yield "你好。"
        yield "继续。"

    await stream_tts_events(
        client=client,
        tts_client=tts_client,
        text_chunks=text_chunks(),
        event_context={"turn_id": "turn-1", "voice_session_id": "session-1"},
        voice="S_test",
        speed_ratio=1.2,
        cancel_event=cancel_event,
        audio_protocol=STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME,
        on_chunk=observed_chunks.append,
    )

    assert tts_client.calls == [
        {
            "text_chunks": ["你好。", "继续。"],
            "voice": "S_test",
            "speed_ratio": 1.2,
            "response_format": "pcm",
            "sample_rate": 16000,
        }
    ]
    assert [event["type"] for event in client.sent] == [
        "audio.format",
        "audio.frame",
        "audio.frame",
        "audio.done",
    ]
    assert client.sent[0]["mime_type"] == "audio/L16"
    assert client.sent[0]["sample_rate"] == 16000
    assert client.sent[0]["channels"] == 1
    assert client.sent[0]["bits_per_sample"] == 16
    assert [base64.b64decode(event["audio"]) for event in client.sent if event["type"] == "audio.frame"] == [
        b"pcm-1",
        b"pcm-2",
    ]
    assert client.sent[-1]["frame_count"] == 2
    assert client.sent[-1]["duration_seconds"] == 0.5
    assert len(observed_chunks) == 3


class FakeEventClient:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeFrameTTS:
    provider_name = "fake-tts"
    model_name = "fake-tts-model"
    default_voice = "S_default"

    def __init__(self):
        self.calls = []

    async def synthesize(self, *args, **kwargs):
        raise AssertionError("stream_tts_events should not call non-streaming TTS")

    async def stream_input_synthesize(
        self,
        text_chunks,
        *,
        voice=None,
        speed_ratio=None,
        response_format=None,
        sample_rate=None,
    ):
        call = {
            "text_chunks": [],
            "voice": voice,
            "speed_ratio": speed_ratio,
            "response_format": response_format,
            "sample_rate": sample_rate,
        }
        self.calls.append(call)
        async for text in text_chunks:
            call["text_chunks"].append(text)
            yield StreamingTTSChunk(
                audio=f"pcm-{len(call['text_chunks'])}".encode(),
                mime_type="audio/L16",
                provider=self.provider_name,
                model=self.model_name,
                voice=voice or self.default_voice,
                speed_ratio=speed_ratio or 1.0,
                sequence=len(call["text_chunks"]),
                sample_rate=sample_rate,
            )
        yield StreamingTTSChunk(
            audio=b"",
            mime_type="audio/L16",
            provider=self.provider_name,
            model=self.model_name,
            voice=voice or self.default_voice,
            speed_ratio=speed_ratio or 1.0,
            sequence=len(call["text_chunks"]) + 1,
            duration_seconds=0.5,
            sample_rate=sample_rate,
            is_final=True,
        )
