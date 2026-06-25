import asyncio
import base64
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import AudioFile, MemoirChatMessage
from app.services.ai_streaming import AITextStreamChunk
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_assistant_turn_stream import (
    AssistantTurnSegmenter,
    VoiceAssistantTurnStreamSession,
)
from app.services.memoir.voice_tts import StreamingTTSChunk
from tests.memoir_helpers import create_test_voice_transcript


async def test_voice_assistant_turn_streams_text_audio_and_persists_message(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我想聊聊第一份工作，当时是在厂里学技术。",
        guidance_mode="guided_interview",
        current_guidance_module="work",
    )
    stream_client = FakeTurnClient()
    ai_client = FakeStreamingAI(
        [
            "好，那我们就接着聊工作。",
            "第一份工作里，",
            "最让您记到现在的是哪一件事？",
        ]
    )
    tts_client = FakeStreamingTTS()
    session = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        ai_client_factory=lambda model=None: ai_client,
        tts_client_factory=lambda: tts_client,
    )

    run_task = asyncio.create_task(session.run())
    ready = await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push(
        {
            "type": "turn.start",
            "ai_model": "fake-model",
            "voice": "S_test_voice",
            "speed_ratio": 1.1,
        }
    )
    started = await _wait_for_event(stream_client, "turn.started")
    completed = await _wait_for_event(stream_client, "turn.completed")
    await asyncio.wait_for(run_task, timeout=1)

    assert ready["state"] == "ready"
    assert started["ai_provider"] == "fake-ai"
    assert completed["state"] == "completed"
    assert completed["text"] == "好，那我们就接着聊工作。第一份工作里，最让您记到现在的是哪一件事？"
    assert completed["primary_intent"] == "follow_up"
    assert completed["audio_file_id"]

    text_deltas = [event["text_delta"] for event in stream_client.sent if event["type"] == "text.delta"]
    assert text_deltas == ai_client.chunks
    text_segments = [event["text"] for event in stream_client.sent if event["type"] == "text.segment"]
    assert text_segments == ["好，那我们就接着聊工作。", "第一份工作里，最让您记到现在的是哪一件事？"]
    audio_chunks = [event for event in stream_client.sent if event["type"] == "audio.chunk"]
    assert [base64.b64decode(event["audio"]) for event in audio_chunks] == [b"audio-0", b"audio-1"]
    assert [call["text"] for call in tts_client.calls] == text_segments
    assert tts_client.calls[0]["voice"] == "S_test_voice"
    assert tts_client.calls[0]["speed_ratio"] == 1.1

    messages = (
        await test_db.execute(
            select(MemoirChatMessage)
            .where(MemoirChatMessage.voice_session_id == created["session"]["id"])
            .order_by(MemoirChatMessage.turn_index)
        )
    ).scalars().all()
    assistant_messages = [message for message in messages if message.role == MemoirChatMessageRole.ASSISTANT.value]
    assert len(assistant_messages) == 1
    assistant_message = assistant_messages[0]
    assert assistant_message.id == completed["message_id"]
    assert assistant_message.audio_file_id == completed["audio_file_id"]
    assert assistant_message.metadata_json["source"] == "ai.streaming_assistant_turn"
    assert assistant_message.metadata_json["turn_pipeline"] == "streaming_llm_tts"
    assert assistant_message.metadata_json["provider"] == "fake-ai"
    assert assistant_message.metadata_json["tts_provider"] == "fake-tts"
    assert assistant_message.metadata_json["tts_duration_seconds"] == 0.4
    assert assistant_message.metadata_json["tts_alignment"][0]["text"] == "好"

    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == completed["audio_file_id"]))
    assert audio_file is not None
    assert audio_file.mime_type == "audio/pcm"
    assert audio_file.size_bytes == len(b"audio-0audio-1")
    audio_relative_path = Path(audio_file.storage_path).relative_to(Path("memoir") / "audio")
    assert (tmp_path / "audio" / audio_relative_path).read_bytes() == b"audio-0audio-1"


async def test_voice_assistant_turn_cancel_does_not_persist_partial_message(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    stream_client = FakeTurnClient()
    session = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        ai_client_factory=lambda model=None: SlowStreamingAI(),
        tts_client_factory=lambda: FakeStreamingTTS(),
    )

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push({"type": "turn.start"})
    await _wait_for_event(stream_client, "turn.started")
    await stream_client.push({"type": "turn.cancel"})
    cancelled = await _wait_for_event(stream_client, "turn.cancelled")
    await asyncio.wait_for(run_task, timeout=1)

    assert cancelled["state"] == "cancelled"
    assistant_count = await test_db.scalar(
        select(func.count(MemoirChatMessage.id))
        .where(
            MemoirChatMessage.voice_session_id == created["session"]["id"],
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
        )
    )
    assert assistant_count == 0


def test_assistant_turn_segmenter_splits_on_sentence_boundary_and_flushes_tail():
    segmenter = AssistantTurnSegmenter(min_chars=4, max_chars=12)

    assert segmenter.push("我们先聊工作。接着说第一份工作里") == ["我们先聊工作。"]
    assert segmenter.flush() == ["接着说第一份工作里"]


async def _wait_for_event(client: "FakeTurnClient", event_type: str) -> dict[str, Any]:
    for _ in range(100):
        for event in client.sent:
            if event["type"] == event_type:
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {event_type}; sent={client.sent}")


class FakeTurnClient:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []

    async def push(self, payload: dict[str, Any]) -> None:
        await self.incoming.put(payload)

    async def receive_json(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class FakeStreamingAI:
    provider_name = "fake-ai"
    model_name = "fake-stream-model"

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def _call_llm(self, *args, **kwargs):
        raise AssertionError("streaming assistant turn should not call non-streaming LLM")

    async def stream_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        for chunk in self.chunks:
            yield AITextStreamChunk(text_delta=chunk)
        yield AITextStreamChunk(finish_reason="stop", usage={"total_tokens": 42})


class SlowStreamingAI(FakeStreamingAI):
    def __init__(self) -> None:
        super().__init__(["这段不会返回"])

    async def stream_llm(self, system_prompt, user_prompt, **kwargs):
        await asyncio.sleep(10)
        yield AITextStreamChunk(text_delta="这段不会返回")


class FakeStreamingTTS:
    provider_name = "fake-tts"
    model_name = "fake-tts-model"
    default_voice = "S_default"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def synthesize(self, *args, **kwargs):
        raise AssertionError("streaming assistant turn should not call non-streaming TTS")

    async def stream_synthesize(self, text, *, voice=None, speed_ratio=None):
        segment_index = len(self.calls)
        self.calls.append({"text": text, "voice": voice, "speed_ratio": speed_ratio})
        yield StreamingTTSChunk(
            audio=f"audio-{segment_index}".encode(),
            mime_type="audio/L16",
            provider=self.provider_name,
            model=self.model_name,
            voice=voice or self.default_voice,
            speed_ratio=speed_ratio or 1.0,
            sequence=1,
        )
        yield StreamingTTSChunk(
            audio=b"",
            mime_type="audio/L16",
            provider=self.provider_name,
            model=self.model_name,
            voice=voice or self.default_voice,
            speed_ratio=speed_ratio or 1.0,
            sequence=2,
            alignment=[TTSAlignmentSegment(text="好", start_ms=0, end_ms=400, text_start=0, text_end=1)],
            duration_seconds=0.4,
            is_final=True,
        )
