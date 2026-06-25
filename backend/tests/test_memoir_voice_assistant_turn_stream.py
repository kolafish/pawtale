import asyncio
import base64
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole
from app.models.memoir import AudioFile, MemoirChatMessage
from app.services.ai_streaming import AITextStreamChunk
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_assistant_turn_stream import VoiceAssistantTurnStreamSession
from app.services.memoir.voice_tts import StreamingTTSChunk
from tests.memoir_helpers import create_test_voice_transcript, post_mobile_memoir_voice_session


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
    assert text_segments == ["好，那我们就接着聊工作。第一份工作里，最让您记到现在的是哪一件事？"]
    audio_chunks = [event for event in stream_client.sent if event["type"] == "audio.chunk"]
    assert [base64.b64decode(event["audio"]) for event in audio_chunks] == [b"audio-0"]
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
    assert audio_file.size_bytes == len(b"audio-0")
    audio_relative_path = Path(audio_file.storage_path).relative_to(Path("memoir") / "audio")
    assert (tmp_path / "audio" / audio_relative_path).read_bytes() == b"audio-0"


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


async def test_voice_assistant_turn_guided_opening_prompt_uses_topic_menu(
    client,
    test_db,
):
    response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "person"},
    )
    voice_session = response.json()["session"]
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=voice_session["id"],
        user_id=user_id,
        chunks=["你想先聊哪一位亲人？"],
        regenerate=True,
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert completed["primary_intent"] == "opening"
    assert ai_client.calls[0]["kwargs"]["max_tokens"] == 420
    assert "主题讲述首次开场" in prompt
    assert "明确介绍可聊主题" in prompt
    assert "人物、事情、家庭、工作、地点、某一天、人生阶段、闲聊" in prompt


async def test_voice_assistant_turn_guided_opening_references_previous_session(
    client,
    test_db,
):
    previous_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "event"},
    )
    previous_session = previous_response.json()["session"]
    previous_message = MemoirChatMessage(
        project_id=previous_session["project_id"],
        voice_session_id=previous_session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text="上次讲到我第一次去上海找工作。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    test_db.add(previous_message)
    await test_db.commit()
    response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "event"},
    )
    voice_session = response.json()["session"]
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, _ = await _run_stream_turn(
        test_db,
        voice_session_id=voice_session["id"],
        user_id=user_id,
        chunks=["欢迎回来，咱们接着聊上次去上海找工作的事，今天想接着讲哪里？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "主题讲述继续开场" in prompt
    assert "叙述者之前已有同模式讲述记录" in prompt
    assert "优先邀请叙述者接着上次的话题继续讲" in prompt
    assert "叙述者：上次讲到我第一次去上海找工作。" in prompt


async def test_voice_assistant_turn_prompt_marks_explicit_topic_switch(
    client,
    test_db,
):
    response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "family"},
    )
    voice_session = response.json()["session"]
    messages = [
        MemoirChatMessage(
            project_id=voice_session["project_id"],
            voice_session_id=voice_session["id"],
            role=MemoirChatMessageRole.USER.value,
            content_text="我小时候经常住在外婆家。",
            content_status=MemoirChatContentStatus.FINAL.value,
            turn_index=0,
            metadata_json={"source": "test"},
        ),
        MemoirChatMessage(
            project_id=voice_session["project_id"],
            voice_session_id=voice_session["id"],
            role=MemoirChatMessageRole.ASSISTANT.value,
            content_text="那我们接着聊外婆。",
            content_status=MemoirChatContentStatus.FINAL.value,
            guidance_intent="follow_up",
            turn_index=1,
            metadata_json={"expects_answer": True},
        ),
        MemoirChatMessage(
            project_id=voice_session["project_id"],
            voice_session_id=voice_session["id"],
            role=MemoirChatMessageRole.USER.value,
            content_text="今天聊聊工作吧。",
            content_status=MemoirChatContentStatus.FINAL.value,
            turn_index=2,
            metadata_json={"source": "test"},
        ),
    ]
    test_db.add_all(messages)
    await test_db.commit()
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, _ = await _run_stream_turn(
        test_db,
        voice_session_id=voice_session["id"],
        user_id=user_id,
        chunks=["好，那我们聊工作。第一份工作里，最让您记到现在的是哪一件事？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert ai_client.calls[0]["kwargs"]["max_tokens"] == 320
    assert "明确选择或切换到“工作”主题" in prompt
    assert "如果用户最新一句明确要求换方向，必须尊重最新一句" in prompt
    assert "下一句必须围绕新主题展开，不要继续旧话题" in prompt
    assert "叙述者：今天聊聊工作吧。" in prompt


async def test_voice_assistant_turn_tts_failure_does_not_persist_partial_message(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="今天我想聊聊朋友吧，不太想讲工作了。",
        guidance_mode="guided_interview",
        current_guidance_module="work",
    )
    stream_client = FakeTurnClient()
    ai_client = FakeStreamingAI(
        [
            "好，那我们就聊聊朋友。",
            "你说到朋友，我想问这些年里有没有一个人让你觉得一见面还是特别亲？",
        ]
    )
    session = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        ai_client_factory=lambda model=None: ai_client,
        tts_client_factory=lambda: FailingStreamingTTS(),
    )

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push({"type": "turn.start"})
    failed = await _wait_for_event(stream_client, "turn.failed")
    await asyncio.wait_for(run_task, timeout=1)

    assert "tts failed" in failed["error"]
    assert [event["type"] for event in stream_client.sent].count("text.segment") == 1
    assert not [event for event in stream_client.sent if event["type"] == "audio.chunk"]
    assistant_count = await test_db.scalar(
        select(func.count(MemoirChatMessage.id))
        .where(
            MemoirChatMessage.voice_session_id == created["session"]["id"],
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
        )
    )
    assert assistant_count == 0


async def _run_stream_turn(
    test_db,
    *,
    voice_session_id: str,
    user_id: str,
    chunks: list[str],
    regenerate: bool = False,
) -> tuple["FakeTurnClient", "FakeStreamingAI", "FakeStreamingTTS", dict[str, Any]]:
    stream_client = FakeTurnClient()
    ai_client = FakeStreamingAI(chunks)
    tts_client = FakeStreamingTTS()
    session = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=voice_session_id,
        user_id=user_id,
        ai_client_factory=lambda model=None: ai_client,
        tts_client_factory=lambda: tts_client,
    )
    run_task = asyncio.create_task(session.run())
    await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push({"type": "turn.start", "regenerate": regenerate})
    completed = await _wait_for_event(stream_client, "turn.completed")
    await asyncio.wait_for(run_task, timeout=1)
    return stream_client, ai_client, tts_client, completed


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


class FailingStreamingTTS(FakeStreamingTTS):
    async def stream_synthesize(self, text, *, voice=None, speed_ratio=None):
        self.calls.append({"text": text, "voice": voice, "speed_ratio": speed_ratio})
        if False:
            yield StreamingTTSChunk(
                audio=b"",
                mime_type="audio/L16",
                provider=self.provider_name,
                model=self.model_name,
                voice=voice or self.default_voice,
            )
        raise RuntimeError("tts failed after text completed")
