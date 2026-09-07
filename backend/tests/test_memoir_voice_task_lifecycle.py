import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services.memoir.voice_assistant_turn_stream import (
    AssistantTurnArtifacts, AssistantTurnStartRequest, ProgressPromptContext, VoiceAssistantTurnStreamSession,
)
from app.services.memoir.voice_tts import VolcTTSClient
from tests.test_memoir_voice_tts import FakeBidirectionalConnection, bidi_server_packet


@pytest.mark.parametrize("ending", ["cancel", "text_error", "audio_error", "normal"])
async def test_assistant_stream_finishes_both_tasks(ending, monkeypatch):
    session = VoiceAssistantTurnStreamSession(db=None, client=None, voice_session_id="session", user_id="user")
    text_started, audio_started = asyncio.Event(), asyncio.Event()
    text_closed, audio_closed = asyncio.Event(), asyncio.Event()
    children = []

    async def text_stream(**kwargs):
        children.append(asyncio.current_task())
        text_started.set()
        try:
            await audio_started.wait()
            if ending == "text_error":
                raise ValueError("text failed")
            if ending != "normal":
                await asyncio.Event().wait()
            await kwargs["text_queue"].put("你好")
        finally:
            text_closed.set()

    async def audio_stream(**kwargs):
        children.append(asyncio.current_task())
        audio_started.set()
        try:
            await text_started.wait()
            if ending == "audio_error":
                raise ValueError("audio failed")
            while await kwargs["text_queue"].get() is not None:
                pass
        finally:
            audio_closed.set()

    monkeypatch.setattr(session, "_stream_text", text_stream)
    monkeypatch.setattr(session, "_stream_audio_from_text_queue", audio_stream)
    task = asyncio.create_task(session._stream_text_and_audio(
        ai_client=None, tts_client=None, system_prompt="", prompt="", max_tokens=1, turn_id="turn",
        req=AssistantTurnStartRequest(), artifacts=AssistantTurnArtifacts(),
        progress_context=ProgressPromptContext(prompt_text=""), cancel_event=asyncio.Event(),
    ))
    try:
        await asyncio.wait_for(text_started.wait(), 1)
        await asyncio.wait_for(audio_started.wait(), 1)
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif ending.endswith("error"):
            with pytest.raises(ValueError, match=ending.replace("_error", " failed")):
                await asyncio.wait_for(task, 1)
        else:
            await asyncio.wait_for(task, 1)
        assert text_closed.is_set() and audio_closed.is_set()
        assert all(child.done() for child in children)
    finally:
        for child in {task, *children}:
            if not child.done():
                child.cancel()
        await asyncio.gather(task, *children, return_exceptions=True)


class TrackedLimiter:
    active = 0

    @asynccontextmanager
    async def slot(self, key, limit):
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


class StalledConnection(FakeBidirectionalConnection):
    async def recv(self):
        if self.packets:
            return self.packets.pop(0)
        await asyncio.Event().wait()


def _tts(monkeypatch, connection):
    limiter = TrackedLimiter()

    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr("app.services.memoir.voice_tts.websockets.connect", connect)
    monkeypatch.setattr("app.services.memoir.voice_tts.provider_concurrency_limiter", limiter)
    client = VolcTTSClient(Settings(_env_file=None, volc_tts_api_key="test-key", tts_timeout_seconds=1))
    return client, limiter


async def _text():
    yield "你好"


@pytest.mark.parametrize("acknowledgements", [0, 1, 2])
async def test_tts_deadline_covers_handshakes_and_audio_receive(monkeypatch, acknowledgements):
    packets = [bidi_server_packet(50), bidi_server_packet(150)]
    connection = StalledConnection(packets[:acknowledgements])
    client, limiter = _tts(monkeypatch, connection)

    async def consume():
        return [chunk async for chunk in client.stream_input_synthesize(_text())]

    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(consume(), 2)
    assert error.value.status_code == 504
    assert connection.closed and limiter.active == 0


async def test_tts_sender_failure_interrupts_stalled_receiver(monkeypatch):
    connection = StalledConnection([bidi_server_packet(50), bidi_server_packet(150)])
    client, limiter = _tts(monkeypatch, connection)
    sender_closed = asyncio.Event()

    async def broken_text():
        try:
            raise TypeError("producer failed")
            yield "unreachable"
        finally:
            sender_closed.set()

    async def consume():
        return [chunk async for chunk in client.stream_input_synthesize(broken_text())]

    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(consume(), 0.5)
    assert isinstance(error.value.__cause__, TypeError)
    assert sender_closed.is_set() and connection.closed and limiter.active == 0


async def test_tts_deadline_does_not_cancel_consumer_between_chunks(monkeypatch):
    connection = StalledConnection([
        bidi_server_packet(50), bidi_server_packet(150),
        bidi_server_packet(352, audio=b"pcm", message_type=0xB4),
    ])
    client, limiter = _tts(monkeypatch, connection)
    stream = client.stream_input_synthesize(_text())
    try:
        assert (await anext(stream)).audio == b"pcm"
        await asyncio.sleep(1.05)
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(anext(stream), 1)
        assert error.value.status_code == 504
    finally:
        await stream.aclose()
    assert connection.closed and limiter.active == 0


async def test_cancelled_tts_releases_sender_connection_and_slot(monkeypatch):
    connection = StalledConnection([bidi_server_packet(50), bidi_server_packet(150)])
    client, limiter = _tts(monkeypatch, connection)
    sender_started, sender_closed = asyncio.Event(), asyncio.Event()

    async def pending_text():
        try:
            sender_started.set()
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            sender_closed.set()

    async def consume():
        return [chunk async for chunk in client.stream_input_synthesize(pending_text())]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(sender_started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sender_closed.is_set() and connection.closed and limiter.active == 0
