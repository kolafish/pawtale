import asyncio
import base64
from typing import Any

from app.services.memoir.streaming_asr import StreamingASREvent
from app.services.memoir.voice_gateway import VoiceGatewaySession


async def test_voice_gateway_streams_audio_and_forwards_asr_events():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    stopped_with: list[str | None] = []
    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_client,
        on_stop=lambda error: _record_stop(stopped_with, error),
    )

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(client, "session.ready")

    await client.push({"type": "session.start"})
    await _wait_for_event(client, "session.started")

    await client.push(
        {
            "type": "audio.frame",
            "audio": base64.b64encode(b"pcm").decode("ascii"),
        }
    )
    partial = await _wait_for_event(client, "asr.partial")

    await client.push({"type": "audio.end"})
    final = await _wait_for_event(client, "asr.final")

    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert asr_client.started
    assert asr_client.sent_audio == [b"pcm"]
    assert asr_client.finished
    assert asr_client.closed
    assert partial["text"] == "我小时候"
    assert final["text"] == "我小时候住在外婆家。"
    assert stopped_with == [None]


async def test_voice_gateway_rejects_invalid_base64_audio_frame():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    session = VoiceGatewaySession(client=client, asr_client_factory=lambda: asr_client)

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(client, "session.ready")

    await client.push({"type": "session.start"})
    await _wait_for_event(client, "session.started")
    await client.push({"type": "audio.frame", "audio": "not-valid-base64"})
    error = await _wait_for_event(client, "session.error")

    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert error["error"] == "audio_frame_invalid_base64"
    assert asr_client.sent_audio == []


async def _record_stop(values: list[str | None], error: str | None) -> None:
    values.append(error)


async def _wait_for_event(client: "FakeGatewayClient", event_type: str) -> dict[str, Any]:
    for _ in range(50):
        for event in client.sent:
            if event["type"] == event_type:
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {event_type}; sent={client.sent}")


class FakeGatewayClient:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None

    async def push(self, payload: dict[str, Any]) -> None:
        await self.incoming.put(payload)

    async def receive_json(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


class FakeStreamingASRClient:
    def __init__(self) -> None:
        self.started = False
        self.finished = False
        self.closed = False
        self.sent_audio: list[bytes] = []
        self.events_queue: asyncio.Queue[StreamingASREvent | None] = asyncio.Queue()

    async def start(self) -> None:
        self.started = True

    async def send_audio(self, audio: bytes) -> None:
        self.sent_audio.append(audio)
        await self.events_queue.put(StreamingASREvent(type="partial", text="我小时候"))

    async def finish(self) -> None:
        self.finished = True
        await self.events_queue.put(StreamingASREvent(type="final", text="我小时候住在外婆家。"))
        await self.events_queue.put(None)

    async def events(self):
        while True:
            event = await self.events_queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self.closed = True
