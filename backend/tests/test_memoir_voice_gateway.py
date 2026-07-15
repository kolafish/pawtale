import asyncio
import base64
import json
import logging
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.domain.memoir.types import (
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.services.memoir import audio_files as memoir_audio_files
from app.services.memoir import voice_transcript_correction
from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.utterance_editing_pipeline import source_text_hash
from app.services.memoir.voice_final_persistence import VoiceFinalPersistenceService
from app.services.memoir.voice_gateway import (
    MAX_AUDIO_FRAME_BYTES,
    VoiceGatewayClientError,
    VoiceGatewaySession,
)
from app.services.memoir.voice_gateway_persistence import VoiceGatewayPersistence
from app.services.memoir.voice_transcripts import CleanedTranscriptText
from tests.memoir_helpers import post_mobile_memoir_voice_session


def test_voice_gateway_normalizes_asr_payload_text_without_rewriting_provider_payload():
    session = VoiceGatewaySession(
        client=FakeGatewayClient(),
        asr_client_factory=lambda: FakeStreamingASRClient(),
    )

    payload = session._asr_event_payload(
        StreamingASREvent(
            type="partial",
            text="现在有几个张杰啊？",
            raw={"provider_text": "现在有几个张杰啊？"},
        ),
        event_type="asr.partial",
    )

    assert payload["text"] == "现在有几个章节啊？"
    assert payload["provider_payload"] == {"provider_text": "现在有几个张杰啊？"}


async def test_voice_gateway_streams_audio_and_forwards_asr_events():
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()
    started_count = {"value": 0}
    stopped_with: list[str | None] = []
    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_client,
        on_start=lambda: _record_start(started_count),
        on_stop=lambda error, _reason: _record_stop(stopped_with, error),
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


async def test_voice_gateway_logs_structured_diagnostics(caplog):
    client = FakeGatewayClient()
    asr_client = FakeStreamingASRClient()

    async def persist_final(_event: StreamingASREvent) -> dict[str, Any]:
        return {
            "message_id": "msg-1",
            "utterance_id": "utt-1",
            "audio_file_id": "audio-1",
        }

    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_client,
        on_asr_final=persist_final,
        connection_id="conn-1",
        diagnostics_context={"voice_session_id": "voice-1"},
    )

    caplog.set_level(logging.INFO, logger="app.services.memoir.voice_gateway")
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
    await _wait_for_event(client, "asr.final")
    await _wait_for_event(client, "audio.ended")
    await client.push({"type": "session.stop"})
    await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    diagnostics = _voice_gateway_diagnostics(caplog.records)
    persisted = [item for item in diagnostics if item["event"] == "asr.final_persisted"]
    assert persisted
    assert persisted[0]["connection_id"] == "conn-1"
    assert persisted[0]["voice_session_id"] == "voice-1"
    assert persisted[0]["frame_count"] == 1
    assert persisted[0]["audio_bytes"] == 3
    assert persisted[0]["message_id"] == "msg-1"
    assert persisted[0]["utterance_id"] == "utt-1"


async def test_voice_gateway_ignores_provider_no_status_close_during_finish():
    class NoStatusCloseAfterFinalASRClient(FakeStreamingASRClient):
        async def finish(self) -> None:
            self.finished = True
            await self.events_queue.put(StreamingASREvent(type="final", text="素材已经保存。"))
            await self.events_queue.put(RuntimeError("(<CloseCode.NO_STATUS_RCVD: 1005>, None)"))  # type: ignore[arg-type]

        async def events(self):
            while True:
                event = await self.events_queue.get()
                if isinstance(event, Exception):
                    raise event
                if event is None:
                    return
                yield event

    client = FakeGatewayClient()
    asr_client = NoStatusCloseAfterFinalASRClient()
    stopped_with: list[str | None] = []

    async def persist_final(_event: StreamingASREvent) -> dict[str, Any]:
        return {"message_id": "msg-1", "utterance_id": "utt-1"}

    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: asr_client,
        on_stop=lambda error, _reason: _record_stop(stopped_with, error),
        on_asr_final=persist_final,
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
    ended = await _wait_for_event(client, "audio.ended")
    await client.push({"type": "session.stop"})
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert final["text"] == "素材已经保存。"
    assert final["message_id"] == "msg-1"
    assert final["utterance_id"] == "utt-1"
    assert ended["state"] == "ready"
    assert stopped["status"] == "stopped"
    assert stopped_with == [None]
    assert not [event for event in client.sent if event["type"] == "session.error"]


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


async def test_voice_gateway_treats_client_no_status_close_as_transport_disconnect():
    class DisconnectedGatewayClient(FakeGatewayClient):
        async def receive_json(self) -> dict[str, Any]:
            raise WebSocketDisconnect(code=1005)

    client = DisconnectedGatewayClient()
    stopped_with: list[tuple[str | None, str]] = []

    async def record_stop(error: str | None, reason: str) -> None:
        stopped_with.append((error, reason))

    session = VoiceGatewaySession(
        client=client,
        asr_client_factory=lambda: FakeStreamingASRClient(),
        on_stop=record_stop,
    )

    await session.run()

    assert stopped_with == [(None, "transport_closed")]
    assert not [event for event in client.sent if event["type"] == "session.error"]
    stopped = [event for event in client.sent if event["type"] == "session.stopped"]
    assert stopped == [{"type": "session.stopped", "state": "stopped", "status": "stopped", "error": ""}]


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


async def test_voice_gateway_persists_latest_partial_when_provider_does_not_mark_final():
    client = FakeGatewayClient()
    partial_texts = [
        "我小时候住在",
        "我小时候住在外婆家。",
    ]
    asr_client = PartialOnlyStreamingASRClient(partial_texts)
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
    final = await _wait_for_event(client, "asr.final")
    ended = await _wait_for_event(client, "audio.ended")
    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert [event["text"] for event in client.sent if event["type"] == "asr.partial"] == partial_texts
    assert [event.text for event in persisted_events] == [partial_texts[-1]]
    assert persisted_events[0].raw["fallback_from_partial"] is True
    assert final["text"] == partial_texts[-1]
    assert final["provider_event_type"] == "partial_fallback"
    assert final["message_id"] == "message-1"
    assert final["utterance_id"] == "utterance-1"
    assert ended["state"] == "ready"


async def test_voice_gateway_streams_provider_final_text_before_audio_end_without_persisting():
    client = FakeGatewayClient()
    asr_client = ImmediateFinalStreamingASRClient("我小时候住在外婆家。")
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
    intermediate = await _wait_for_event(client, "asr.partial")

    assert intermediate["text"] == "我小时候住在外婆家。"
    assert intermediate["state"] == "listening"
    assert intermediate["provider_event_type"] == "final"
    assert persisted_events == []
    assert [event for event in client.sent if event["type"] == "asr.final"] == []

    await client.push({"type": "audio.end"})
    final = await _wait_for_event(client, "asr.final")
    await _wait_for_event(client, "audio.ended")
    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert final["turn_final"] is True
    assert final["segment_final"] is True
    assert final["message_id"] == "message-1"
    assert [event.text for event in persisted_events] == ["我小时候住在外婆家。"]


async def test_voice_gateway_persists_latest_online_cumulative_final_once_at_audio_end():
    client = FakeGatewayClient()
    final_texts = [
        "要不然讲讲大学阶段吧。我大学上了7年，但是我上了大学就没怎么上学。",
        "要不然讲讲大学阶段吧。我大学上了7年，但是我上了大学就没怎么上学。天天就净玩了，所以学习成绩也不怎么好。",
    ]
    asr_client = OnlineCumulativeFinalStreamingASRClient(final_texts)
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
    for audio in (b"pcm-1", b"pcm-2"):
        await client.push(
            {
                "type": "audio.frame",
                "audio": base64.b64encode(audio).decode("ascii"),
            }
        )

    intermediate_events = await _wait_for_event_count(client, "asr.partial", 2)

    assert [event["text"] for event in intermediate_events] == final_texts
    assert [event["provider_event_type"] for event in intermediate_events] == ["final", "final"]
    assert persisted_events == []
    assert [event for event in client.sent if event["type"] == "asr.final"] == []

    await client.push({"type": "audio.end"})
    final = await _wait_for_event(client, "asr.final")
    await _wait_for_event(client, "audio.ended")
    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert [event.text for event in persisted_events] == [final_texts[-1]]
    assert final["text"] == final_texts[-1]
    assert final["turn_final"] is True
    assert final["message_id"] == "message-1"


async def test_voice_gateway_persists_latest_partial_once_at_audio_end():
    client = FakeGatewayClient()
    long_partial = "小时候在外婆家的院子里摘菜，夏天一起剥青豆。" * 10
    asr_client = PartialOnlyStreamingASRClient([long_partial])
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
    partial = await _wait_for_event(client, "asr.partial")

    assert partial["text"] == long_partial
    assert persisted_events == []
    assert [event for event in client.sent if event["type"] == "asr.final"] == []

    await client.push({"type": "audio.end"})
    final = await _wait_for_event(client, "asr.final")
    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert final["turn_final"] is True
    assert final["provider_event_type"] == "partial_fallback"
    assert [event.text for event in persisted_events] == [long_partial]


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
        on_stop=lambda error, _reason: _record_stop(stopped_with, error),
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
        on_stop=lambda error, _reason: _record_stop(stopped_with, error),
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


async def test_book_voice_command_gateway_uses_transient_realtime_session(monkeypatch):
    from app.routers.memoir import voice_gateway as voice_gateway_router

    calls: dict[str, Any] = {}

    class FakeUser:
        id = "user-1"

    class FakeWebSocket:
        def __init__(self) -> None:
            self.accepted = False

        async def accept(self) -> None:
            self.accepted = True

    async def fake_authenticate(websocket: Any, db: Any) -> FakeUser:
        calls["auth"] = (websocket, db)
        return FakeUser()

    async def fake_load_project(db: Any, project_id: str, user_id: str) -> object:
        calls["load_project"] = (db, project_id, user_id)
        return object()

    class FakeLease:
        def __init__(self) -> None:
            self.released = False

        async def release(self) -> None:
            self.released = True

    lease = FakeLease()

    class FakeCapacityManager:
        async def acquire(self, user_id: str, session_id: str) -> FakeLease:
            calls["capacity"] = (user_id, session_id)
            return lease

    class FakeBookGatewaySession:
        def __init__(self, **kwargs: Any) -> None:
            calls["gateway_kwargs"] = kwargs

        async def run(self) -> None:
            calls["asr_client"] = calls["gateway_kwargs"]["asr_client_factory"]()

    def fake_create_asr_client(user_id: str, session_id: str) -> FakeStreamingASRClient:
        calls["asr_factory"] = (user_id, session_id)
        return FakeStreamingASRClient()

    websocket = FakeWebSocket()
    db = object()
    monkeypatch.setattr(voice_gateway_router, "_authenticate_websocket", fake_authenticate)
    monkeypatch.setattr(voice_gateway_router, "load_memoir_project", fake_load_project)
    monkeypatch.setattr(voice_gateway_router, "voice_gateway_capacity_manager", FakeCapacityManager())
    monkeypatch.setattr(voice_gateway_router, "VoiceGatewaySession", FakeBookGatewaySession)
    monkeypatch.setattr(voice_gateway_router, "create_realtime_volc_streaming_asr_client", fake_create_asr_client)

    await voice_gateway_router.mobile_memoir_book_voice_command_gateway(websocket, "project-1", db)

    transient_session_id = calls["capacity"][1]
    assert websocket.accepted
    assert calls["auth"] == (websocket, db)
    assert calls["load_project"] == (db, "project-1", "user-1")
    assert transient_session_id.startswith("book-voice-command-project-1-")
    assert calls["capacity"] == ("user-1:book-voice-command", transient_session_id)
    assert calls["asr_factory"] == ("user-1", transient_session_id)
    assert set(calls["gateway_kwargs"]) == {
        "client",
        "asr_client_factory",
        "connection_id",
        "diagnostics_context",
    }
    assert calls["gateway_kwargs"]["client"] is websocket
    assert calls["gateway_kwargs"]["connection_id"]
    assert calls["gateway_kwargs"]["diagnostics_context"] == {
        "gateway": "book_voice_command",
        "project_id": "project-1",
        "voice_session_id": transient_session_id,
    }
    assert isinstance(calls["asr_client"], FakeStreamingASRClient)
    assert lease.released


async def test_mobile_voice_gateway_transport_close_preserves_pending_audio_without_failing_session(monkeypatch):
    from app.routers.memoir import voice_gateway as voice_gateway_router

    calls: dict[str, Any] = {"logs": []}

    class FakeUser:
        id = "user-1"

    class FakeWebSocket:
        def __init__(self) -> None:
            self.accepted = False

        async def accept(self) -> None:
            self.accepted = True

    async def fake_authenticate(websocket: Any, db: Any) -> FakeUser:
        calls["auth"] = (websocket, db)
        return FakeUser()

    async def fake_get_voice_session_detail(db: Any, voice_session_id: str, user_id: str) -> object:
        calls["detail"] = (db, voice_session_id, user_id)
        return object()

    async def fake_stop_voice_session(db: Any, voice_session_id: str, user_id: str, request: Any) -> object:
        calls["stop"] = (db, voice_session_id, user_id, request.error_message)
        return object()

    class FakeLease:
        def __init__(self) -> None:
            self.released = False

        async def release(self) -> None:
            self.released = True

    lease = FakeLease()

    class FakeCapacityManager:
        async def acquire(self, user_id: str, session_id: str) -> FakeLease:
            calls["capacity"] = (user_id, session_id)
            return lease

    class FakePersistence:
        pending_audio_path = "/tmp/recoverable.pcm.part"
        pending_audio_size_bytes = 4096

        def __init__(self, **kwargs: Any) -> None:
            calls["persistence_kwargs"] = kwargs
            self.discard_calls = 0
            calls["persistence"] = self

        async def set_turn_context(self, _context: Any) -> None:
            return None

        async def record_audio_frame(self, _audio: bytes) -> None:
            return None

        async def persist_final(self, _event: StreamingASREvent) -> dict[str, Any]:
            return {}

        async def flush_unfinalized_audio(self) -> dict[str, Any] | None:
            raise RuntimeError("database unavailable")

        async def discard_pending_audio(self) -> None:
            self.discard_calls += 1

    class FakeGatewaySession:
        def __init__(self, **kwargs: Any) -> None:
            calls["gateway_kwargs"] = kwargs

        async def run(self) -> None:
            await calls["gateway_kwargs"]["on_stop"](None, "transport_closed")

    def fake_log(event: str, **payload: Any) -> None:
        calls["logs"].append({"event": event, **payload})

    websocket = FakeWebSocket()
    db = object()
    monkeypatch.setattr(voice_gateway_router, "_authenticate_websocket", fake_authenticate)
    monkeypatch.setattr(
        voice_gateway_router.memoir_voice_sessions,
        "get_voice_session_detail",
        fake_get_voice_session_detail,
    )
    monkeypatch.setattr(voice_gateway_router.memoir_voice_sessions, "stop_voice_session", fake_stop_voice_session)
    monkeypatch.setattr(voice_gateway_router, "voice_gateway_capacity_manager", FakeCapacityManager())
    monkeypatch.setattr(voice_gateway_router, "VoiceGatewayPersistence", FakePersistence)
    monkeypatch.setattr(voice_gateway_router, "VoiceGatewaySession", FakeGatewaySession)
    monkeypatch.setattr(voice_gateway_router, "_log_voice_gateway_router_event", fake_log)

    await voice_gateway_router.mobile_memoir_voice_gateway(websocket, "voice-1", db)

    persistence = calls["persistence"]
    assert websocket.accepted
    assert persistence.discard_calls == 0
    assert lease.released
    assert "stop" not in calls
    assert calls["logs"][0]["event"] == "websocket.accepted"
    assert calls["logs"][1]["event"] == "unfinalized_audio.flush_failed"
    assert calls["logs"][1]["pending_audio_size_bytes"] == 4096
    assert calls["logs"][2]["event"] == "pending_audio.preserved"
    assert calls["logs"][2]["pending_audio_path"] == "/tmp/recoverable.pcm.part"


async def test_voice_gateway_persistence_saves_final_message_utterance_and_audio(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")

    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]
    project_id = create_response.json()["session"]["project_id"]
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=voice_session_id,
        user_id=user_response.json()["id"],
    )

    await persistence.record_audio_frame(b"pcm-1")
    await persistence.record_audio_frame(b"pcm-2")
    pending_path = persistence.pending_audio_path
    assert pending_path is not None
    assert pending_path.exists()
    assert pending_path.read_bytes() == b"pcm-1pcm-2"
    assert persistence.pending_audio_size_bytes == len(b"pcm-1pcm-2")

    persisted = await persistence.persist_final(
        StreamingASREvent(
            type="final",
            text="嗯，我小时候就是住在外婆家。",
            start_ms=0,
            end_ms=2400,
            raw={"payload": {"result": {"text": "嗯，我小时候就是住在外婆家。"}}},
        )
    )

    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == persisted["audio_file_id"]))
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == persisted["message_id"]))
    utterance = await test_db.scalar(select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == persisted["utterance_id"]))
    raw_version = await test_db.scalar(
        select(MemoirTranscriptVersion).where(MemoirTranscriptVersion.id == persisted["transcript_version_id"])
    )
    editing_jobs = (
        await test_db.execute(
            select(MemoirEditingJob).where(
                MemoirEditingJob.project_id == project_id
            )
        )
    ).scalars().all()
    jobs_by_type = {job.job_type: job for job in editing_jobs}
    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion)
            .where(MemoirTranscriptVersion.voice_session_id == voice_session_id)
            .order_by(MemoirTranscriptVersion.created_at)
        )
    ).scalars().all()

    assert audio_file is not None
    assert audio_file.mime_type == "audio/pcm"
    assert audio_file.size_bytes == len(b"pcm-1pcm-2")
    assert (tmp_path / "audio" / audio_file.user_id / f"{audio_file.id}.pcm").read_bytes() == b"pcm-1pcm-2"
    assert not pending_path.exists()
    assert persistence.pending_audio_path is None
    assert persistence.pending_audio_size_bytes == 0
    assert message is not None
    assert raw_version is not None
    assert message.content_text == "嗯，我小时候就是住在外婆家。"
    assert message.content_status == "final"
    assert message.audio_file_id == audio_file.id
    assert message.transcript_version_id == raw_version.id
    assert message.metadata_json["asr_transcript_version_id"] == versions[0].id
    assert message.metadata_json["auto_corrected"] is False
    assert message.metadata_json["correction_status"] == "pending"
    assert utterance is not None
    assert utterance.chat_message_id == message.id
    assert utterance.audio_file_id == audio_file.id
    assert utterance.transcript_raw == "嗯，我小时候就是住在外婆家。"
    assert utterance.transcript_edited == ""
    assert len(versions) == 1
    assert versions[0].voice_session_id == voice_session_id
    assert versions[0].content == "嗯，我小时候就是住在外婆家。"
    assert versions[0].version_type == "asr_raw"
    assert versions[0].created_by == "asr"
    correction_job = jobs_by_type.get(MemoirEditingJobType.CORRECT_TRANSCRIPT.value)
    assert correction_job is not None
    assert correction_job.status == MemoirEditingJobStatus.PENDING.value
    assert correction_job.dedupe_key == f"correct_transcript:{utterance.id}"
    assert correction_job.payload_json["utterance_id"] == utterance.id
    assert correction_job.payload_json["raw_transcript_version_id"] == raw_version.id
    assert MemoirEditingJobType.ROUTE_SOURCES.value not in jobs_by_type
    assert MemoirEditingJobType.QUALITY_AUDIT.value not in jobs_by_type


async def test_voice_gateway_persistence_saves_human_interviewer_turn_as_ignored_context(
    client,
    test_db,
):
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={
            "guidance_mode": "free_narration",
            "interview_type": "human_interview",
            "provider": "volc_streaming_asr",
        },
    )
    session = create_response.json()["session"]
    service = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=session["id"],
        user_id=user_response.json()["id"],
    )

    interviewer = await service.persist_final(
        StreamingASREvent(
            type="final",
            text="你小时候家里有几口人？",
            start_ms=0,
            end_ms=1200,
            raw={"payload": {"result": {"text": "你小时候家里有几口人？"}}},
        ),
        speaker_role="interviewer",
    )
    narrator = await service.persist_final(
        StreamingASREvent(
            type="final",
            text="我们家那时候有六口人。",
            start_ms=1300,
            end_ms=3000,
            raw={"payload": {"result": {"text": "我们家那时候有六口人。"}}},
        ),
        speaker_role="narrator",
    )

    interviewer_message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == interviewer.message_id)
    )
    interviewer_utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == interviewer.utterance_id)
    )
    narrator_message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == narrator.message_id)
    )
    editing_jobs = (
        await test_db.execute(
            select(MemoirEditingJob).where(MemoirEditingJob.project_id == session["project_id"])
        )
    ).scalars().all()

    assert interviewer_message is not None
    assert interviewer_message.role == "interviewer"
    assert interviewer_message.parent_message_id is None
    assert interviewer_message.metadata_json["speaker_role"] == "interviewer"
    assert interviewer_message.metadata_json["interview_type"] == "human_interview"
    assert interviewer_message.metadata_json["correction_status"] == "skipped"
    assert interviewer_utterance is not None
    assert interviewer_utterance.source_material_type == "interviewer_prompt"
    assert interviewer_utterance.source_route_action == "ignore"
    assert narrator_message is not None
    assert narrator_message.role == "user"
    assert narrator_message.parent_message_id == interviewer_message.id
    assert narrator_message.metadata_json["speaker_role"] == "narrator"
    assert [job.job_type for job in editing_jobs] == [MemoirEditingJobType.CORRECT_TRANSCRIPT.value]
    assert editing_jobs[0].payload_json["utterance_id"] == narrator.utterance_id


async def test_voice_gateway_persistence_rejects_interviewer_role_for_ai_session(
    client,
    test_db,
):
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    session = create_response.json()["session"]
    service = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=session["id"],
        user_id=user_response.json()["id"],
    )

    with pytest.raises(HTTPException) as exc:
        await service.persist_final(
            StreamingASREvent(
                type="final",
                text="你小时候家里有几口人？",
                start_ms=0,
                end_ms=1200,
                raw={"payload": {"result": {"text": "你小时候家里有几口人？"}}},
            ),
            speaker_role="interviewer",
        )

    messages = (await test_db.execute(select(MemoirChatMessage))).scalars().all()
    utterances = (await test_db.execute(select(MemoirVoiceUtterance))).scalars().all()
    assert exc.value.status_code == 400
    assert exc.value.detail == "只有人工访谈支持采访者录音"
    assert messages == []
    assert utterances == []


async def test_voice_transcript_correction_job_updates_saved_raw_turn(
    client,
    test_db,
    monkeypatch,
):
    async def fake_generate_cleaned_transcript_text(text: str, *, strict: bool = True, **kwargs):
        assert text == "嗯，我小时候就是住在外婆家。"
        assert strict is False
        assert kwargs["context"].scene == "voice_chat"
        assert kwargs["context"].guidance_mode == "free_narration"
        return CleanedTranscriptText(
            content="我小时候住在外婆家。",
            provider="fake-ai",
            created_by=MemoirCreatedBy.AI,
        )

    monkeypatch.setattr(
        "app.services.memoir.voice_transcript_correction.generate_cleaned_transcript_text",
        fake_generate_cleaned_transcript_text,
    )
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]
    service = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=voice_session_id,
        user_id=user_response.json()["id"],
    )

    persisted = await service.persist_final(
        StreamingASREvent(
            type="final",
            text="嗯，我小时候就是住在外婆家。",
            start_ms=0,
            end_ms=2400,
            raw={"payload": {"result": {"text": "嗯，我小时候就是住在外婆家。"}}},
        )
    )
    correction_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.CORRECT_TRANSCRIPT.value
        )
    )

    assert correction_job is not None
    result = await voice_transcript_correction.run_transcript_correction_job(test_db, correction_job)
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == persisted.message_id))
    utterance = await test_db.scalar(select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == persisted.utterance_id))
    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion)
            .where(MemoirTranscriptVersion.voice_session_id == voice_session_id)
            .order_by(MemoirTranscriptVersion.version_type)
        )
    ).scalars().all()
    corrected_versions = [
        version for version in versions
        if version.version_type == MemoirTranscriptVersionType.DIALECT_CORRECTED.value
    ]

    assert result.status == MemoirEditingJobStatus.SUCCEEDED
    assert result.result_json["utterance_id"] == persisted.utterance_id
    assert message is not None
    assert message.content_text == "我小时候住在外婆家。"
    assert message.content_status == "corrected"
    assert message.metadata_json["auto_corrected"] is True
    assert message.metadata_json["correction_status"] == "succeeded"
    assert message.metadata_json["correction_provider"] == "fake-ai"
    assert utterance is not None
    assert utterance.transcript_raw == "嗯，我小时候就是住在外婆家。"
    assert utterance.transcript_edited == "我小时候住在外婆家。"
    assert len(corrected_versions) == 1
    assert corrected_versions[0].content == "我小时候住在外婆家。"
    assert corrected_versions[0].provider == "fake-ai"
    assert message.transcript_version_id == corrected_versions[0].id
    route_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.ROUTE_SOURCES.value
        )
    )
    assert route_job is not None
    assert route_job.status == MemoirEditingJobStatus.PENDING.value
    assert route_job.dedupe_key == f"route_source:{utterance.id}"
    assert route_job.payload_json["utterance_ids"] == [utterance.id]
    assert route_job.payload_json["source"] == "transcript_correction_succeeded"
    assert route_job.payload_json["transcript_version_ids"] == {
        utterance.id: corrected_versions[0].id,
    }
    assert route_job.payload_json["source_text_hashes"] == {
        utterance.id: source_text_hash("我小时候住在外婆家。"),
    }
    assert route_job.payload_json["source_text_lengths"] == {
        utterance.id: len("我小时候住在外婆家。"),
    }
    assert result.result_json["route_source_job_id"] == route_job.id
    source_card_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_SOURCE_CARD.value
        )
    )
    assert source_card_job is not None
    assert source_card_job.dedupe_key == f"generate_source_card:{utterance.id}"
    assert source_card_job.payload_json["utterance_id"] == utterance.id
    assert source_card_job.payload_json["reason"] == "transcript_correction_succeeded"
    assert result.result_json["source_card_job_id"] == source_card_job.id


async def test_voice_transcript_correction_job_does_not_overwrite_user_edit(
    client,
    test_db,
    monkeypatch,
):
    async def fail_generate_cleaned_transcript_text(*_args, **_kwargs):
        raise AssertionError("correction should be skipped after a user edit")

    monkeypatch.setattr(
        "app.services.memoir.voice_transcript_correction.generate_cleaned_transcript_text",
        fail_generate_cleaned_transcript_text,
    )
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]
    service = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=voice_session_id,
        user_id=user_response.json()["id"],
    )
    persisted = await service.persist_final(
        StreamingASREvent(
            type="final",
            text="嗯，我小时候就是住在外婆家。",
        )
    )
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == persisted.message_id))
    utterance = await test_db.scalar(select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == persisted.utterance_id))
    assert message is not None
    assert utterance is not None
    user_version = MemoirTranscriptVersion(
        voice_session_id=voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.USER_EDITED.value,
        content="用户自己整理后的文字。",
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(user_version)
    await test_db.flush()
    utterance.transcript_edited = "用户自己整理后的文字。"
    message.content_text = "用户自己整理后的文字。"
    message.transcript_version_id = user_version.id
    test_db.add_all([utterance, message])
    await test_db.commit()
    correction_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.CORRECT_TRANSCRIPT.value
        )
    )

    assert correction_job is not None
    result = await voice_transcript_correction.run_transcript_correction_job(test_db, correction_job)
    await test_db.refresh(message)
    await test_db.refresh(utterance)

    assert result.status == MemoirEditingJobStatus.SKIPPED
    assert result.error_message == "transcript already edited"
    assert message.content_text == "用户自己整理后的文字。"
    assert message.transcript_version_id == user_version.id
    assert utterance.transcript_edited == "用户自己整理后的文字。"
    route_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.ROUTE_SOURCES.value
        )
    )
    assert route_job is not None
    assert route_job.payload_json["source"] == "transcript_already_edited"
    assert route_job.payload_json["transcript_version_ids"] == {utterance.id: user_version.id}
    assert route_job.payload_json["source_text_hashes"] == {
        utterance.id: source_text_hash("用户自己整理后的文字。"),
    }
    source_card_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_SOURCE_CARD.value
        )
    )
    assert source_card_job is not None
    assert source_card_job.dedupe_key == f"generate_source_card:{utterance.id}"
    assert source_card_job.payload_json["reason"] == "transcript_already_edited"
    assert result.result_json["source_card_job_id"] == source_card_job.id


async def test_voice_gateway_persistence_rejects_audio_stream_over_limit(client, test_db, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MAX_AUDIO_BYTES", 5)
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
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


async def test_voice_gateway_persistence_flushes_unfinalized_audio_from_pending_file(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=voice_session_id,
        user_id=user_response.json()["id"],
    )

    await persistence.record_audio_frame(b"partial-pcm")
    pending_path = persistence.pending_audio_path
    assert pending_path is not None
    assert pending_path.exists()

    persisted = await persistence.flush_unfinalized_audio()

    assert persisted is not None
    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == persisted["audio_file_id"]))
    utterance = await test_db.scalar(select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == persisted["utterance_id"]))

    assert audio_file is not None
    assert audio_file.size_bytes == len(b"partial-pcm")
    assert (tmp_path / "audio" / audio_file.user_id / f"{audio_file.id}.pcm").read_bytes() == b"partial-pcm"
    assert utterance is not None
    assert utterance.transcript_raw == ""
    assert utterance.provider_payload_json == {"source": "unfinalized_audio"}
    assert not pending_path.exists()
    assert persistence.pending_audio_path is None


async def test_voice_gateway_persistence_keeps_pending_audio_when_final_persist_fails(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=create_response.json()["session"]["id"],
        user_id=user_response.json()["id"],
    )
    await persistence.record_audio_frame(b"recoverable-pcm")
    pending_path = persistence.pending_audio_path
    assert pending_path is not None

    original_load_voice_session = persistence._service._load_voice_session

    async def fail_load_voice_session():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(persistence._service, "_load_voice_session", fail_load_voice_session)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await persistence.persist_final(
            StreamingASREvent(type="final", text="这段本来已经识别出来。", raw={"source": "test"})
        )

    assert pending_path.exists()
    assert pending_path.read_bytes() == b"recoverable-pcm"
    assert persistence.pending_audio_path == pending_path
    assert persistence.pending_audio_size_bytes == len(b"recoverable-pcm")

    monkeypatch.setattr(persistence._service, "_load_voice_session", original_load_voice_session)
    persisted = await persistence.flush_unfinalized_audio()

    assert persisted is not None
    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == persisted["audio_file_id"]))
    assert audio_file is not None
    assert (tmp_path / "audio" / audio_file.user_id / f"{audio_file.id}.pcm").read_bytes() == b"recoverable-pcm"
    assert not pending_path.exists()
    assert persistence.pending_audio_path is None


async def test_voice_gateway_persistence_discards_pending_audio_file(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=create_response.json()["session"]["id"],
        user_id=user_response.json()["id"],
    )

    await persistence.record_audio_frame(b"abandoned-pcm")
    pending_path = persistence.pending_audio_path
    assert pending_path is not None
    assert pending_path.exists()

    await persistence.discard_pending_audio()

    assert not pending_path.exists()
    assert persistence.pending_audio_path is None
    assert persistence.pending_audio_size_bytes == 0


async def test_voice_final_persistence_rolls_back_rows_and_staged_audio_on_failure(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user_response = await client.get("/api/auth/me")
    create_response = await post_mobile_memoir_voice_session(
        client,
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


async def test_voice_final_persistence_rolls_back_staged_audio_path_on_failure(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user_response = await client.get("/api/auth/me")
    user_id = user_response.json()["id"]
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]
    service = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=voice_session_id,
        user_id=user_id,
    )
    source_path = tmp_path / "pending.pcm"
    source_path.write_bytes(b"pcm-from-path")
    staged_audio = await memoir_audio_files.stage_audio_path(
        test_db,
        user_id,
        source_path,
        mime_type="audio/pcm",
        extension=".pcm",
        move_source=True,
    )

    async def fail_turn_index(_voice_session_id: str) -> int:
        raise RuntimeError("turn index failed")

    monkeypatch.setattr(service, "_next_turn_index", fail_turn_index)

    with pytest.raises(RuntimeError, match="turn index failed"):
        await service.persist_final(
            StreamingASREvent(type="final", text="我小时候住在外婆家。"),
            staged_audio=staged_audio,
        )

    audio_files = (await test_db.execute(select(AudioFile))).scalars().all()
    messages = (await test_db.execute(select(MemoirChatMessage))).scalars().all()
    utterances = (await test_db.execute(select(MemoirVoiceUtterance))).scalars().all()
    transcript_versions = (await test_db.execute(select(MemoirTranscriptVersion))).scalars().all()

    assert audio_files == []
    assert messages == []
    assert utterances == []
    assert transcript_versions == []
    assert not source_path.exists()
    assert not staged_audio.path.exists()


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


def _voice_gateway_diagnostics(records) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    prefix = "memoir_voice_gateway "
    for record in records:
        message = record.getMessage()
        if not message.startswith(prefix):
            continue
        diagnostics.append(json.loads(message.removeprefix(prefix)))
    return diagnostics


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


class OnlineCumulativeFinalStreamingASRClient(FakeStreamingASRClient):
    def __init__(self, final_texts: list[str]) -> None:
        super().__init__()
        self.final_texts = list(final_texts)
        self.index = 0

    async def send_audio(self, audio: bytes) -> None:
        self.sent_audio.append(audio)
        if self.index >= len(self.final_texts):
            return
        await self.events_queue.put(StreamingASREvent(type="final", text=self.final_texts[self.index]))
        self.index += 1

    async def finish(self) -> None:
        self.finished = True
        await self.events_queue.put(None)


class ImmediateFinalStreamingASRClient(FakeStreamingASRClient):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    async def send_audio(self, audio: bytes) -> None:
        self.sent_audio.append(audio)
        await self.events_queue.put(StreamingASREvent(type="final", text=self.text))


class PartialOnlyStreamingASRClient(FakeStreamingASRClient):
    def __init__(self, partial_texts: list[str]) -> None:
        super().__init__()
        self.partial_texts = partial_texts

    async def send_audio(self, audio: bytes) -> None:
        self.sent_audio.append(audio)
        for text in self.partial_texts:
            await self.events_queue.put(StreamingASREvent(type="partial", text=text))

    async def finish(self) -> None:
        self.finished = True
        await self.events_queue.put(None)


class FailingStartStreamingASRClient(FakeStreamingASRClient):
    async def start(self) -> None:
        raise RuntimeError("provider connect failed")
