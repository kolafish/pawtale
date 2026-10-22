import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services.memoir.interview_agent import InterviewConfig, InterviewInput, prepare_interview_turn
from app.services.memoir.voice_followup_ai import InterviewDialogue
from app.services.memoir.voice_assistant_turn_stream import (
    AssistantTurnArtifacts,
    AssistantTurnStartRequest,
    VoiceAssistantTurnStreamSession,
)
from app.services.memoir.voice_tts import VolcTTSClient
from app.services.memoir.streaming_tts_events import chunk_text_for_tts, stream_tts_events
from tests.test_memoir_voice_tts import FakeBidirectionalConnection, bidi_server_packet, decode_bidi_client_packet


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
        ai_client=None, tts_client=None, prepared=prepare_interview_turn(InterviewInput(InterviewDialogue("open_narration", ())), InterviewConfig()), turn_id="turn",
        req=AssistantTurnStartRequest(), artifacts=AssistantTurnArtifacts(),
        cancel_event=asyncio.Event(),
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
            yield "你好"
            raise TypeError("producer failed")
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


@pytest.mark.parametrize("has_first_text", [False, True])
async def test_cancelled_tts_releases_sender_connection_and_slot(monkeypatch, has_first_text):
    connection = StalledConnection([bidi_server_packet(50), bidi_server_packet(150)])
    client, limiter = _tts(monkeypatch, connection)
    sender_started, sender_closed = asyncio.Event(), asyncio.Event()

    async def pending_text():
        try:
            if has_first_text:
                yield "你好"
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
    assert sender_closed.is_set() and limiter.active == 0
    assert connection.closed == has_first_text
    assert bool(connection.sent) == has_first_text


async def test_tts_waits_for_first_text_before_opening_provider_session(monkeypatch):
    connection = FakeBidirectionalConnection([
        bidi_server_packet(50), bidi_server_packet(150),
        bidi_server_packet(352, audio=b"pcm", message_type=0xB4), bidi_server_packet(152),
    ])
    client, limiter = _tts(monkeypatch, connection)
    text_waiting, text_ready = asyncio.Event(), asyncio.Event()

    async def delayed_text():
        yield ""
        text_waiting.set()
        await text_ready.wait()
        yield "您好。"
        yield "您怎么称呼？"

    async def consume():
        return [chunk async for chunk in client.stream_input_synthesize(delayed_text())]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(text_waiting.wait(), 1)
        # Exceed the TTS deadline while the LLM is still preparing text.
        await asyncio.sleep(1.05)
        assert not task.done()
        assert limiter.active == 0 and not connection.sent
        text_ready.set()
        chunks = await asyncio.wait_for(task, 1)
        assert b"".join(chunk.audio for chunk in chunks) == b"pcm"
        requests = [decode_bidi_client_packet(frame) for frame in connection.sent]
        assert [p["payload"]["req_params"]["text"] for p in requests if p["event"] == 200] == [
            "您好。", "您怎么称呼？",
        ]
        assert connection.closed and limiter.active == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("ending", ["empty", "error"])
async def test_tts_without_first_text_does_not_open_provider_session(monkeypatch, ending):
    connection = FakeBidirectionalConnection([])
    client, limiter = _tts(monkeypatch, connection)

    async def text():
        yield ""
        if ending == "error":
            raise ValueError("text generation failed")

    with pytest.raises(HTTPException if ending == "empty" else ValueError):
        async for _ in client.stream_input_synthesize(text()):
            pytest.fail("No text must not produce audio")
    assert not connection.sent and limiter.active == 0


async def test_cancelled_turn_waits_for_both_children(monkeypatch):
    session = VoiceAssistantTurnStreamSession(db=None, client=None, voice_session_id="session", user_id="user")
    started = [asyncio.Event(), asyncio.Event()]
    closed = [asyncio.Event(), asyncio.Event()]
    children = []

    async def child(index):
        children.append(asyncio.current_task())
        try:
            started[index].set()
            await asyncio.Event().wait()
        finally:
            closed[index].set()

    monkeypatch.setattr(session, "_wait_for_cancel", lambda *args: child(0))
    monkeypatch.setattr(session, "_stream_turn", lambda *args: child(1))
    task = asyncio.create_task(session._run_turn(AssistantTurnStartRequest()))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(event.is_set() for event in closed)
    finally:
        for child_task in (task, *children):
            child_task.cancel()
        await asyncio.gather(task, *children, return_exceptions=True)


@pytest.mark.parametrize("ending", ["cancel", "send_error"])
async def test_client_send_exit_closes_tts_stream(monkeypatch, ending):
    connection = StalledConnection([
        bidi_server_packet(50), bidi_server_packet(150),
        bidi_server_packet(352, audio=b"pcm", message_type=0xB4),
    ])
    tts, limiter = _tts(monkeypatch, connection)
    sending = asyncio.Event()

    class Client:
        async def send_json(self, payload):
            sending.set()
            if ending == "send_error":
                raise ConnectionError("disconnected")
            await asyncio.Event().wait()

    task = asyncio.create_task(stream_tts_events(
        client=Client(), tts_client=tts, text_chunks=_text(), event_context={},
        voice=None, speed_ratio=None, cancel_event=asyncio.Event(),
    ))
    await asyncio.wait_for(sending.wait(), 1)
    if ending == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ConnectionError):
            await task
    assert connection.closed and limiter.active == 0


async def test_closing_text_chunks_waits_for_pending_input_cleanup():
    closed = asyncio.Event()

    async def text():
        yield "短句"
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    chunks = chunk_text_for_tts(text(), asyncio.Event(), max_wait_seconds=0.01)
    assert await anext(chunks) == "短句"
    await chunks.aclose()
    assert closed.is_set()


async def test_client_send_failure_closes_text_upstream(monkeypatch):
    from app.services.ai_streaming import AITextStreamChunk

    closed = False
    class AI:
        async def stream_llm(self, *args, **kwargs):
            nonlocal closed
            try:
                yield AITextStreamChunk(text_delta="[listen]\n您好，怎么称呼您？")
                await asyncio.Event().wait()
            finally:
                closed = True

    class Client:
        async def send_json(self, payload):
            raise ConnectionError("client disconnected")

    monkeypatch.setattr("app.services.ai_calls.record_ai_usage_in_background", lambda **kwargs: None)
    session = VoiceAssistantTurnStreamSession(db=None, client=Client(), voice_session_id="session", user_id="user")
    with pytest.raises(ConnectionError):
        await session._stream_text(
            ai_client=AI(), prepared=prepare_interview_turn(InterviewInput(InterviewDialogue("open_narration", ())), InterviewConfig()),
            turn_id="turn", artifacts=AssistantTurnArtifacts(), cancel_event=asyncio.Event(), text_queue=asyncio.Queue(),
        )
    assert closed
