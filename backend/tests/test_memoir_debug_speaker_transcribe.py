import asyncio
import base64
from typing import Any

from app.domain.memoir.types import MemoirCreatedBy
from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.debug_speaker_transcribe import (
    DebugSpeakerProofreadContextLine,
    DebugSpeakerTranscribeSession,
    proofread_debug_speaker_transcript_text,
    speaker_segments_from_asr_event,
)
from app.services.memoir.voice_transcripts import CleanedTranscriptText


def test_debug_speaker_segments_extract_provider_speaker_utterances():
    event = StreamingASREvent(
        type="final",
        text="你小时候住在哪里？住在厂里的家属院。",
        raw={
            "payload": {
                "result": [
                    {
                        "utterances": [
                            {
                                "text": "你小时候住在哪里？",
                                "speaker_id": "1",
                                "start_time": 0,
                                "end_time": 1600,
                                "confidence": 0.81,
                            },
                            {
                                "text": "住在厂里的家属院。",
                                "speaker_id": "2",
                                "start_time": 1800,
                                "end_time": 3600,
                                "confidence": 0.88,
                            },
                        ],
                    }
                ]
            }
        },
    )

    segments = speaker_segments_from_asr_event(event)

    assert [(segment.speaker_id, segment.text) for segment in segments] == [
        ("speaker_1", "你小时候住在哪里？"),
        ("speaker_2", "住在厂里的家属院。"),
    ]
    assert segments[0].start_ms == 0
    assert segments[1].end_ms == 3600
    assert segments[1].confidence == 0.88


def test_debug_speaker_segments_do_not_invent_speakers_without_provider_labels():
    event = StreamingASREvent(
        type="final",
        text="我小时候住在厂里的家属院。",
        raw={"payload": {"result": [{"text": "我小时候住在厂里的家属院。"}]}},
    )

    assert speaker_segments_from_asr_event(event) == []


def test_debug_speaker_segments_extract_single_result_speaker_label():
    event = StreamingASREvent(
        type="partial",
        text="先讲讲小时候吧。",
        raw={
            "payload": {
                "result": [
                    {
                        "text": "先讲讲小时候吧。",
                        "speaker": "host",
                        "start_ms": 200,
                        "end_ms": 1800,
                    }
                ]
            }
        },
    )

    segments = speaker_segments_from_asr_event(event)

    assert len(segments) == 1
    assert segments[0].speaker_id == "speaker_host"
    assert segments[0].text == "先讲讲小时候吧。"
    assert segments[0].start_ms == 200
    assert segments[0].end_ms == 1800


def test_debug_speaker_segments_extract_volc_additions_speaker_label():
    event = StreamingASREvent(
        type="final",
        text="先讲讲小时候吧。住在厂里的家属院。",
        raw={
            "payload": {
                "result": [
                    {
                        "utterances": [
                            {
                                "text": "先讲讲小时候吧。",
                                "additions": {"speaker": "1", "gender": "male"},
                                "start_time": 0,
                                "end_time": 1200,
                            },
                            {
                                "text": "住在厂里的家属院。",
                                "additions": "{\"speaker\":\"2\",\"gender\":\"女声\"}",
                                "start_time": 1500,
                                "end_time": 3200,
                            },
                        ],
                    }
                ]
            }
        },
    )

    segments = speaker_segments_from_asr_event(event)

    assert [(segment.speaker_id, segment.text) for segment in segments] == [
        ("speaker_1", "先讲讲小时候吧。"),
        ("speaker_2", "住在厂里的家属院。"),
    ]
    assert [segment.speaker_gender for segment in segments] == ["male", "female"]


async def test_debug_speaker_transcribe_session_streams_speaker_windows_without_persistence():
    client = FakeGatewayClient()
    transcriber = FakeSpeakerTranscriber()
    session = DebugSpeakerTranscribeSession(
        client=client,
        transcriber_factory=lambda: transcriber,
    )

    run_task = asyncio.create_task(session.run())
    ready = await _wait_for_event(client, "session.ready")
    await client.push({"type": "session.start"})
    started = await _wait_for_event(client, "session.started")
    await client.push(
        {
            "type": "audio.frame",
            "audio": base64.b64encode(b"pcm").decode("ascii"),
        }
    )
    speaker_events = await _wait_for_event_count(client, "speaker.partial", 2)
    diarization = await _wait_for_event(client, "speaker.diarization")
    await client.push({"type": "audio.end"})
    ended = await _wait_for_event(client, "audio.ended")
    await client.push({"type": "session.stop"})
    stopped = await _wait_for_event(client, "session.stopped")
    await asyncio.wait_for(run_task, timeout=1)

    assert ready["state"] == "ready"
    assert started["state"] == "listening"
    assert transcriber.sent_audio == [b"pcm"]
    assert [(event["speaker_id"], event["text"]) for event in speaker_events] == [
        ("speaker_1", "你小时候住在哪里？"),
        ("speaker_2", "住在厂里的家属院。"),
    ]
    assert [event.get("speaker_gender") for event in speaker_events] == [None, "female"]
    assert diarization["status"] == "available"
    assert speaker_events[0]["provider_event_type"] == "partial"
    assert ended["state"] == "ready"
    assert stopped["status"] == "stopped"
    assert transcriber.closed


async def test_debug_speaker_transcribe_session_falls_back_to_unknown_speaker():
    client = FakeGatewayClient()
    transcriber = FakeSpeakerTranscriber(with_speaker_labels=False)
    session = DebugSpeakerTranscribeSession(
        client=client,
        transcriber_factory=lambda: transcriber,
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
    event = await _wait_for_event(client, "speaker.partial")
    diarization = await _wait_for_event(client, "speaker.diarization")
    await client.push({"type": "session.stop"})
    await asyncio.wait_for(run_task, timeout=1)

    assert event["speaker_id"] == "speaker_unknown"
    assert event["text"] == "我小时候住在厂里的家属院。"
    assert diarization["status"] == "missing_provider_labels"


async def test_debug_speaker_proofread_uses_recent_context(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_generate_cleaned_transcript_text(
        source_text: str,
        *,
        ai_model: str | None = None,
        context: Any = None,
        strict: bool = True,
    ) -> CleanedTranscriptText:
        captured["source_text"] = source_text
        captured["ai_model"] = ai_model
        captured["context"] = context
        captured["strict"] = strict
        return CleanedTranscriptText(
            content="四川话得不得行吗？",
            provider="fake",
            created_by=MemoirCreatedBy.AI,
        )

    monkeypatch.setattr(
        "app.services.memoir.debug_speaker_transcribe.generate_cleaned_transcript_text",
        fake_generate_cleaned_transcript_text,
    )

    result = await proofread_debug_speaker_transcript_text(
        "四川话得不得行嘛？",
        context_lines=[
            DebugSpeakerProofreadContextLine(
                speaker_id="speaker_1",
                text="来，我们先试一下四川话识别。",
                start_ms=0,
                end_ms=3000,
            ),
            DebugSpeakerProofreadContextLine(
                speaker_id="speaker_2",
                text="刚才讲的是四川话。",
                start_ms=3100,
                end_ms=4500,
            ),
        ],
        ai_model="doubao-test",
    )

    assert result.content == "四川话得不得行吗？"
    assert captured["source_text"] == "四川话得不得行嘛？"
    assert captured["ai_model"] == "doubao-test"
    assert captured["strict"] is False
    assert captured["context"].scene == "多人实时转写调试"
    assert captured["context"].recent_user_utterances == (
        "说话人 1：来，我们先试一下四川话识别。",
        "说话人 2：刚才讲的是四川话。",
    )


async def test_debug_speaker_proofread_endpoint_requires_login_and_returns_cleaned_text(
    anonymous_client,
    monkeypatch,
):
    captured: dict[str, Any] = {}

    async def fake_proofread_debug_speaker_transcript_text(
        text: str,
        *,
        context_lines: list[DebugSpeakerProofreadContextLine],
        ai_model: str | None = None,
    ) -> CleanedTranscriptText:
        captured["text"] = text
        captured["context_lines"] = context_lines
        captured["ai_model"] = ai_model
        return CleanedTranscriptText(
            content="四川话得不得行吗？",
            provider="fake",
            created_by=MemoirCreatedBy.AI,
        )

    monkeypatch.setattr(
        "app.routers.memoir.debug_speaker.proofread_debug_speaker_transcript_text",
        fake_proofread_debug_speaker_transcript_text,
    )

    unauthorized = await anonymous_client.post(
        "/api/mobile/memoir/debug/speaker-transcribe/proofread",
        json={"text": "四川话得不得行嘛？"},
    )
    assert unauthorized.status_code == 401

    register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "debug-speaker@example.com",
            "password": "password123",
            "display_name": "调试用户",
        },
    )
    assert register.status_code == 201

    response = await anonymous_client.post(
        "/api/mobile/memoir/debug/speaker-transcribe/proofread",
        json={
            "text": "四川话得不得行嘛？",
            "context_lines": [
                {
                    "speaker_id": "speaker_1",
                    "text": "来，我们先试一下四川话识别。",
                    "start_ms": 0,
                    "end_ms": 3000,
                }
            ],
            "ai_model": "doubao-test",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "四川话得不得行嘛？",
        "proofread_text": "四川话得不得行吗？",
        "changed": True,
        "provider": "fake",
        "context_line_count": 1,
    }
    assert captured["text"] == "四川话得不得行嘛？"
    assert captured["ai_model"] == "doubao-test"
    assert captured["context_lines"] == [
        DebugSpeakerProofreadContextLine(
            speaker_id="speaker_1",
            text="来，我们先试一下四川话识别。",
            start_ms=0,
            end_ms=3000,
        )
    ]


async def _wait_for_event(client: "FakeGatewayClient", event_type: str) -> dict[str, Any]:
    events = await _wait_for_event_count(client, event_type, 1)
    return events[0]


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

    async def push(self, payload: dict[str, Any]) -> None:
        await self.incoming.put(payload)

    async def receive_json(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class FakeSpeakerTranscriber:
    def __init__(self, *, with_speaker_labels: bool = True) -> None:
        self.with_speaker_labels = with_speaker_labels
        self.started = False
        self.finished = False
        self.closed = False
        self.sent_audio: list[bytes] = []
        self.events_queue: asyncio.Queue[StreamingASREvent | None] = asyncio.Queue()

    async def start(self) -> None:
        self.started = True

    async def send_audio(self, audio: bytes) -> None:
        self.sent_audio.append(audio)
        if self.with_speaker_labels:
            await self.events_queue.put(
                StreamingASREvent(
                    type="partial",
                    text="你小时候住在哪里？住在厂里的家属院。",
                    raw={
                        "payload": {
                            "result": [
                                {
                                    "utterances": [
                                        {"text": "你小时候住在哪里？", "speaker_id": "1"},
                                        {"text": "住在厂里的家属院。", "speaker_id": "2", "gender": "female"},
                                    ]
                                }
                            ]
                        }
                    },
                )
            )
        else:
            await self.events_queue.put(
                StreamingASREvent(
                    type="partial",
                    text="我小时候住在厂里的家属院。",
                    raw={"payload": {"result": [{"text": "我小时候住在厂里的家属院。"}]}},
                )
            )

    async def finish(self) -> None:
        self.finished = True
        await self.events_queue.put(None)

    async def events(self):
        while True:
            event = await self.events_queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self.closed = True
