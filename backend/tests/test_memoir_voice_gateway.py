import asyncio
import base64
from typing import Any

import pytest
from sqlalchemy import select

from app.models.memoir import AudioFile, MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.voice_final_persistence import VoiceFinalPersistenceService
from app.services.memoir.voice_gateway import (
    MAX_AUDIO_FRAME_BYTES,
    VoiceGatewayClientError,
    VoiceGatewaySession,
)
from app.services.memoir.voice_gateway_persistence import VoiceGatewayPersistence


async def test_voice_gateway_streams_audio_and_forwards_asr_events():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    started_count = {"value": 0}
    stopped_with: list[str | None] = []
    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_client,
        on_start=lambda: _record_start(started_count),
        on_stop=lambda error: _record_stop(stopped_with, error),
    )

    run_task = asyncio.create_task(session.run())
    ready = await _wait_for_event(client, "session.ready")
    assert ready["state"] == "ready"

    await client.push({"type": "session.start"})
    started = await _wait_for_event(client, "session.started")
    assert started["state"] == "listening"

    await client.push(
        {
            "type": "audio.frame",
            "audio": base64.b64encode(b"pcm").decode("ascii"),
        }
    )
    partial = await _wait_for_event(client, "asr.partial")

    await client.push({"type": "audio.end"})
    final = await _wait_for_event(client, "asr.final")
    ended = await _wait_for_event(client, "audio.ended")

    await client.push({"type": "session.stop"})
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert asr_client.started
    assert started_count["value"] == 1
    assert asr_client.sent_audio == [b"pcm"]
    assert asr_client.finished
    assert asr_client.closed
    assert partial["text"] == "我小时候"
    assert partial["state"] == "listening"
    assert final["text"] == "我小时候住在外婆家。"
    assert final["state"] == "finishing"
    assert ended["state"] == "ready"
    assert stopped["state"] == "stopped"
    assert stopped_with == [None]


async def test_voice_gateway_rejects_audio_before_session_start_with_ready_state():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    session = VoiceGatewaySession(client=client, asr_client_factory=lambda: asr_client)

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(client, "session.ready")

    await client.push(
        {
            "type": "audio.frame",
            "audio": base64.b64encode(b"pcm").decode("ascii"),
        }
    )
    error = await _wait_for_event(client, "session.error")
    await client.push({"type": "session.stop"})
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert error["error"] == "asr_session_not_started"
    assert error["state"] == "ready"
    assert asr_client.sent_audio == []
    assert stopped["state"] == "stopped"


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
    assert error["state"] == "listening"
    assert asr_client.sent_audio == []


async def test_voice_gateway_rejects_oversized_audio_frame():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    session = VoiceGatewaySession(client=client, asr_client_factory=lambda: asr_client)

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(client, "session.ready")

    await client.push({"type": "session.start"})
    await _wait_for_event(client, "session.started")
    await client.push(
        {
            "type": "audio.frame",
            "audio": base64.b64encode(b"x" * (MAX_AUDIO_FRAME_BYTES + 1)).decode("ascii"),
        }
    )
    error = await _wait_for_event(client, "session.error")
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert error["error"] == "audio_frame_too_large"
    assert error["state"] == "failed"
    assert stopped["status"] == "failed"
    assert asr_client.sent_audio == []


async def test_voice_gateway_duplicate_start_reuses_current_listening_session():
    client = FakeGatewayClient()
    asr_clients = [FakeStreamingASRClient(), FakeStreamingASRClient()]
    created_count = {"value": 0}

    def create_asr_client() -> FakeStreamingASRClient:
        client = asr_clients[created_count["value"]]
        created_count["value"] += 1
        return client

    session = VoiceGatewaySession(client=client, asr_client_factory=create_asr_client)

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(client, "session.ready")
    await client.push({"type": "session.start"})
    first_started = await _wait_for_event_count(client, "session.started", 1)
    await client.push({"type": "session.start"})
    second_started = await _wait_for_event_count(client, "session.started", 2)
    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert created_count["value"] == 1
    assert asr_clients[0].started
    assert asr_clients[0].finished
    assert asr_clients[0].closed
    assert not asr_clients[1].started
    assert [event["state"] for event in first_started] == ["listening"]
    assert [event["state"] for event in second_started] == ["listening", "listening"]


async def test_voice_gateway_start_failure_reports_failed_state():
    client = FakeGatewayClient()
    asr_client = FailingStartStreamingASRClient()
    session = VoiceGatewaySession(client=client, asr_client_factory=lambda: asr_client)

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(client, "session.ready")
    await client.push({"type": "session.start"})
    error = await _wait_for_event(client, "session.error")
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert error["error"] == "provider connect failed"
    assert error["state"] == "failed"
    assert stopped["status"] == "failed"
    assert stopped["state"] == "failed"
    assert asr_client.closed


async def test_voice_gateway_final_event_includes_persistence_ids():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    recorded_audio: list[bytes] = []
    persisted_events: list[StreamingASREvent] = []
    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_client,
        on_audio_frame=lambda audio: _record_audio(recorded_audio, audio),
        on_asr_final=lambda event: _persist_fake_final(persisted_events, event),
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
    await client.push({"type": "audio.end"})
    final = await _wait_for_event(client, "asr.final")
    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert recorded_audio == [b"pcm"]
    assert [event.text for event in persisted_events] == ["我小时候住在外婆家。"]
    assert final["message_id"] == "message-1"
    assert final["utterance_id"] == "utterance-1"
    assert final["audio_file_id"] == "audio-1"
    assert final["transcript_version_id"] == "transcript-1"


async def test_voice_gateway_persists_only_latest_cumulative_final_per_audio_segment():
    client = FakeGatewayClient()
    final_texts = [
        "我小时候住在外婆家。",
        "我小时候住在外婆家，门口有一棵树。",
        "我小时候住在外婆家，门口有一棵树，夏天大家都在树下乘凉。",
    ]
    asr_client = CumulativeFinalStreamingASRClient(final_texts)
    persisted_events: list[StreamingASREvent] = []
    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_client,
        on_asr_final=lambda event: _persist_fake_final(persisted_events, event),
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
    await client.push({"type": "audio.end"})
    ended = await _wait_for_event(client, "audio.ended")
    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    partial_final_candidates = [
        event["text"]
        for event in client.sent
        if event["type"] == "asr.partial" and event.get("provider_event_type") == "final"
    ]
    final_events = [event for event in client.sent if event["type"] == "asr.final"]

    assert partial_final_candidates == final_texts
    assert [event.text for event in persisted_events] == [final_texts[-1]]
    assert len(final_events) == 1
    assert final_events[0]["text"] == final_texts[-1]
    assert final_events[0]["message_id"] == "message-1"
    assert ended["state"] == "ready"


async def test_voice_gateway_stop_finishes_asr_before_shutdown():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    session = VoiceGatewaySession(client=client, asr_client_factory=lambda: asr_client)

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
    await client.push({"type": "session.stop"})

    final = await _wait_for_event(client, "asr.final")
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert asr_client.finished
    assert asr_client.closed
    assert final["text"] == "我小时候住在外婆家。"
    assert stopped["status"] == "stopped"


async def test_voice_gateway_can_start_new_asr_after_audio_end():
    client = FakeGatewayClient()
    asr_clients = [FakeStreamingASRClient(), FakeStreamingASRClient()]
    started_count = {"value": 0}
    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_clients[started_count["value"]],
        on_start=lambda: _record_start(started_count),
    )

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(client, "session.ready")

    await client.push({"type": "session.start"})
    await _wait_for_event_count(client, "session.started", 1)
    await client.push(
        {
            "type": "audio.frame",
            "audio": base64.b64encode(b"first").decode("ascii"),
        }
    )
    await client.push({"type": "audio.end"})
    await _wait_for_event_count(client, "audio.ended", 1)

    await client.push({"type": "session.start"})
    await _wait_for_event_count(client, "session.started", 2)
    await client.push(
        {
            "type": "audio.frame",
            "audio": base64.b64encode(b"second").decode("ascii"),
        }
    )
    await client.push({"type": "audio.end"})
    await _wait_for_event_count(client, "audio.ended", 2)

    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert started_count["value"] == 2
    assert asr_clients[0].sent_audio == [b"first"]
    assert asr_clients[0].finished
    assert asr_clients[0].closed
    assert asr_clients[1].sent_audio == [b"second"]
    assert asr_clients[1].finished
    assert asr_clients[1].closed


async def test_voice_gateway_provider_error_stops_session_with_error():
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
    await asr_client.events_queue.put(StreamingASREvent(type="error", error="provider_down"))

    error = await _wait_for_event(client, "session.error")
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert error["error"] == "provider_down"
    assert stopped["status"] == "failed"
    assert stopped["error"] == "provider_down"
    assert stopped_with == ["provider_down"]


async def test_voice_gateway_masks_final_persistence_errors():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    stopped_with: list[str | None] = []

    async def fail_persist(_event: StreamingASREvent) -> dict[str, Any]:
        raise RuntimeError("sqlite raw insert failure should not reach the mini program")

    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_client,
        on_stop=lambda error: _record_stop(stopped_with, error),
        on_asr_final=fail_persist,
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
    await client.push({"type": "audio.end"})

    error = await _wait_for_event(client, "session.error")
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert error["error"] == "asr_final_persistence_failed"
    assert "sqlite raw insert" not in str(error)
    assert stopped["status"] == "failed"
    assert stopped_with == ["asr_final_persistence_failed"]


async def test_voice_gateway_persistence_saves_final_message_utterance_and_audio(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user_response = await client.get("/api/auth/me")
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=voice_session_id,
        user_id=user_response.json()["id"],
    )

    await persistence.record_audio_frame(b"pcm-1")
    await persistence.record_audio_frame(b"pcm-2")
    persisted = await persistence.persist_final(
        StreamingASREvent(
            type="final",
            text="我小时候住在外婆家。",
            start_ms=0,
            end_ms=2400,
            raw={"payload": {"result": {"text": "我小时候住在外婆家。"}}},
        )
    )

    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == persisted["audio_file_id"]))
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == persisted["message_id"]))
    utterance = await test_db.scalar(select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == persisted["utterance_id"]))
    transcript_version = await test_db.scalar(
        select(MemoirTranscriptVersion).where(MemoirTranscriptVersion.id == persisted["transcript_version_id"])
    )

    assert audio_file is not None
    assert audio_file.mime_type == "audio/pcm"
    assert audio_file.size_bytes == len(b"pcm-1pcm-2")
    assert (tmp_path / "audio" / audio_file.user_id / f"{audio_file.id}.pcm").read_bytes() == b"pcm-1pcm-2"
    assert message is not None
    assert transcript_version is not None
    assert message.content_text == "我小时候住在外婆家。"
    assert message.audio_file_id == audio_file.id
    assert message.transcript_version_id == transcript_version.id
    assert utterance is not None
    assert utterance.chat_message_id == message.id
    assert utterance.audio_file_id == audio_file.id
    assert utterance.transcript_raw == "我小时候住在外婆家。"
    assert transcript_version.answer_id is None
    assert transcript_version.voice_session_id == voice_session_id
    assert transcript_version.content == "我小时候住在外婆家。"
    assert transcript_version.version_type == "asr_raw"
    assert transcript_version.created_by == "asr"


async def test_voice_gateway_persistence_rejects_audio_stream_over_limit(client, test_db, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MAX_AUDIO_BYTES", 5)
    user_response = await client.get("/api/auth/me")
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=create_response.json()["session"]["id"],
        user_id=user_response.json()["id"],
    )

    await persistence.record_audio_frame(b"123")
    with pytest.raises(VoiceGatewayClientError) as exc_info:
        await persistence.record_audio_frame(b"456")

    assert exc_info.value.code == "audio_stream_too_large"


async def test_voice_final_persistence_rolls_back_rows_and_staged_audio_on_failure(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user_response = await client.get("/api/auth/me")
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]
    service = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=voice_session_id,
        user_id=user_response.json()["id"],
    )

    async def fail_turn_index(_voice_session_id: str) -> int:
        raise RuntimeError("turn index failed")

    monkeypatch.setattr(service, "_next_turn_index", fail_turn_index)

    with pytest.raises(RuntimeError, match="turn index failed"):
        await service.persist_final(
            StreamingASREvent(type="final", text="我小时候住在外婆家。"),
            audio=b"pcm",
        )

    audio_files = (await test_db.execute(select(AudioFile))).scalars().all()
    messages = (await test_db.execute(select(MemoirChatMessage))).scalars().all()
    utterances = (await test_db.execute(select(MemoirVoiceUtterance))).scalars().all()
    transcript_versions = (await test_db.execute(select(MemoirTranscriptVersion))).scalars().all()

    assert audio_files == []
    assert messages == []
    assert utterances == []
    assert transcript_versions == []
    assert list((tmp_path / "audio").rglob("*.pcm")) == []


async def _record_start(values: dict[str, int]) -> None:
    values["value"] += 1


async def _record_stop(values: list[str | None], error: str | None) -> None:
    values.append(error)


async def _record_audio(values: list[bytes], audio: bytes) -> None:
    values.append(audio)


async def _persist_fake_final(
    values: list[StreamingASREvent],
    event: StreamingASREvent,
) -> dict[str, str]:
    values.append(event)
    return {
        "message_id": "message-1",
        "utterance_id": "utterance-1",
        "audio_file_id": "audio-1",
        "transcript_version_id": "transcript-1",
    }


async def _wait_for_event(client: "FakeGatewayClient", event_type: str) -> dict[str, Any]:
    for _ in range(50):
        for event in client.sent:
            if event["type"] == event_type:
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {event_type}; sent={client.sent}")


async def _wait_for_event_count(client: "FakeGatewayClient", event_type: str, count: int) -> list[dict[str, Any]]:
    for _ in range(50):
        events = [event for event in client.sent if event["type"] == event_type]
        if len(events) >= count:
            return events
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {count} {event_type}; sent={client.sent}")


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


class CumulativeFinalStreamingASRClient(FakeStreamingASRClient):
    def __init__(self, final_texts: list[str]) -> None:
        super().__init__()
        self.final_texts = final_texts

    async def finish(self) -> None:
        self.finished = True
        for text in self.final_texts:
            await self.events_queue.put(StreamingASREvent(type="final", text=text))
        await self.events_queue.put(None)


class FailingStartStreamingASRClient(FakeStreamingASRClient):
    async def start(self) -> None:
        raise RuntimeError("provider connect failed")
