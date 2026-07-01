import asyncio
import base64
import json
from typing import Any

from sqlalchemy import select

from app.models.memoir import MemoirBookPlanChapter
from app.services.memoir.book_voice_assistant_turn_stream import (
    BookVoiceAssistantTurnStartRequest,
    BookVoiceAssistantTurnStreamSession,
)
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_tts import StreamingTTSChunk
from tests.memoir_helpers import create_test_book_plan, ensure_mobile_memoir_project


async def test_book_voice_assistant_turn_streams_ai_preview_and_audio(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapter = await create_test_book_plan(test_db, project_id=project_id)
    user_id = (await client.get("/api/auth/me")).json()["id"]
    stream_client = FakeBookTurnClient()
    ai_client = FakeBookVoiceTurnAI(
        {
            "intent": "rename_chapter",
            "chapter_ref": "第一章",
            "new_title": "童年往事",
            "summary": "准备把第一章改名为童年往事",
        }
    )
    tts_client = FakeBookTurnStreamingTTS()
    session = BookVoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        project_id=project_id,
        user_id=user_id,
        ai_client_factory=lambda model=None: ai_client,
        tts_client_factory=lambda: tts_client,
    )

    run_task = asyncio.create_task(session.run())
    ready = await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push(
        {
            "type": "turn.start",
            "text": "把第一章改名为童年往事",
            "ai_model": "fake-command-model",
            "voice": "S_book_voice",
            "speed_ratio": 1.15,
        }
    )
    started = await _wait_for_event(stream_client, "turn.started")
    preview = await _wait_for_event(stream_client, "command.preview")
    completed = await _wait_for_event(stream_client, "turn.completed")
    await asyncio.wait_for(run_task, timeout=1)

    assert ready["state"] == "ready"
    assert started["turn_pipeline"] == "book_voice_editing_turn_stream"
    assert preview["command_preview"]["intent"] == "rename_chapter"
    assert preview["command_preview"]["operations"][0]["chapter_id"] == chapter.id
    assert preview["execute_after_reply"] is False
    assert completed["state"] == "completed"
    assert completed["message_id"] == completed["turn_id"]
    assert completed["audio_file_id"] is None
    assert completed["command_preview"]["intent"] == "rename_chapter"
    assert completed["execution_result"] is None
    assert completed["turn_pipeline"] == "book_voice_editing_turn_stream"
    assert completed["text"].startswith("我理解了，")
    assert "确认执行" in completed["text"]
    assert tts_client.calls[0]["text_chunks"] == [completed["text"]]
    assert tts_client.calls[0]["voice"] == "S_book_voice"
    assert tts_client.calls[0]["speed_ratio"] == 1.15
    assert tts_client.calls[0]["response_format"] == "pcm"

    text_delta = await _event(stream_client, "text.delta")
    assert text_delta["text"] == completed["text"]
    frame = await _event(stream_client, "audio.frame")
    assert base64.b64decode(frame["audio"]) == b"book-audio-0-1"
    done = await _event(stream_client, "audio.done")
    assert done["frame_count"] == 1
    assert "1. 外婆家的童年" in ai_client.calls[0]["user_prompt"]


async def test_book_voice_assistant_confirmation_executes_after_reply(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapter = await create_test_book_plan(test_db, project_id=project_id)
    user_id = (await client.get("/api/auth/me")).json()["id"]
    stream_client = FakeBookTurnClient()
    tts_client = FakeBookTurnStreamingTTS()
    session = BookVoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        project_id=project_id,
        user_id=user_id,
        ai_client_factory=lambda model=None: FakeBookVoiceTurnAI({}),
        tts_client_factory=lambda: tts_client,
    )

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push(
        {
            "type": "turn.start",
            "text": "确认执行。",
            "pending_command": _rename_preview(chapter.id),
        }
    )
    preview = await _wait_for_event(stream_client, "command.preview")
    text_delta = await _wait_for_event(stream_client, "text.delta")
    done = await _wait_for_event(stream_client, "audio.done")
    completed = await _wait_for_event(stream_client, "turn.completed")
    await asyncio.wait_for(run_task, timeout=1)

    assert preview["execute_after_reply"] is True
    assert text_delta["text"] == "好的，我按刚才理解的修改。"
    assert done["frame_count"] == 1
    assert completed["execution_result"]["intent"] == "rename_chapter"
    assert completed["execution_result"]["plan"]["chapters"][0]["title"] == "童年往事"

    renamed = await test_db.scalar(select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id))
    assert renamed.title == "童年往事"


async def test_book_voice_assistant_confirmation_cancel_does_not_execute(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapter = await create_test_book_plan(test_db, project_id=project_id)
    user_id = (await client.get("/api/auth/me")).json()["id"]
    stream_client = FakeBookTurnClient()
    tts_client = SlowBookTurnStreamingTTS()
    session = BookVoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        project_id=project_id,
        user_id=user_id,
        ai_client_factory=lambda model=None: FakeBookVoiceTurnAI({}),
        tts_client_factory=lambda: tts_client,
    )

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push(
        {
            "type": "turn.start",
            "text": "确认执行。",
            "pending_command": _rename_preview(chapter.id),
        }
    )
    await _wait_for_event(stream_client, "command.preview")
    await _wait_for_event(stream_client, "audio.frame")
    await stream_client.push({"type": "turn.cancel", "reason": "interrupted_by_user"})
    cancelled = await _wait_for_event(stream_client, "turn.cancelled")
    await asyncio.wait_for(run_task, timeout=1)

    assert cancelled["state"] == "interrupted"
    assert cancelled["reason"] == "interrupted_by_user"
    assert not any(event["type"] == "turn.completed" for event in stream_client.sent)

    unchanged = await test_db.scalar(select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id))
    assert unchanged.title == "外婆家的童年"


async def test_book_voice_assistant_cancelled_stream_completion_sends_cancelled(test_db):
    stream_client = FakeBookTurnClient()
    session = BookVoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        project_id="project/voice-race",
        user_id="user/1",
    )

    async def fake_wait_for_cancel(cancel_event):
        await asyncio.sleep(10)

    async def fake_stream_turn(turn_id, req, cancel_event):
        cancel_event.set()
        return False

    session._wait_for_cancel = fake_wait_for_cancel  # type: ignore[method-assign]
    session._stream_turn = fake_stream_turn  # type: ignore[method-assign]

    await session._run_turn(BookVoiceAssistantTurnStartRequest(text="确认执行。"))

    cancelled = await _wait_for_event(stream_client, "turn.cancelled")
    assert cancelled["state"] == "cancelled"
    assert cancelled["reason"] == "cancelled"


async def _wait_for_event(client: "FakeBookTurnClient", event_type: str) -> dict[str, Any]:
    for _ in range(100):
        event = await _event(client, event_type)
        if event:
            return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {event_type}; sent={client.sent}")


async def _event(client: "FakeBookTurnClient", event_type: str) -> dict[str, Any] | None:
    for event in client.sent:
        if event["type"] == event_type:
            return event
    return None


def _rename_preview(chapter_id: str) -> dict[str, Any]:
    return {
        "original_text": "把第一章改名为童年往事",
        "intent": "rename_chapter",
        "intent_label": "修改章名",
        "response_kind": "action_preview",
        "summary": "准备把「外婆家的童年」改名为「童年往事」。",
        "operations": [
            {
                "type": "rename_chapter",
                "chapter_id": chapter_id,
                "chapter_title": "外婆家的童年",
                "new_title": "童年往事",
            }
        ],
        "requires_confirmation": True,
        "used_ai": True,
        "provider": "fake-ai",
        "model": "fake-book-command",
    }


class FakeBookTurnClient:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []

    async def push(self, payload: dict[str, Any]) -> None:
        await self.incoming.put(payload)

    async def receive_json(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class FakeBookVoiceTurnAI:
    provider_name = "fake-ai"
    model_name = "fake-book-command"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return json.dumps(self.payload, ensure_ascii=False)


class FakeBookTurnStreamingTTS:
    provider_name = "fake-tts"
    model_name = "fake-tts-model"
    default_voice = "S_default"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def synthesize(self, *args, **kwargs):
        raise AssertionError("book voice assistant turn stream should not call non-streaming TTS")

    async def stream_synthesize(self, *args, **kwargs):
        raise AssertionError("book voice assistant turn stream should use bidirectional TTS")

    async def stream_input_synthesize(
        self,
        text_chunks,
        *,
        voice=None,
        speed_ratio=None,
        response_format=None,
        sample_rate=None,
    ):
        segment_index = len(self.calls)
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
                audio=f"book-audio-{segment_index}-{len(call['text_chunks'])}".encode(),
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
            alignment=[TTSAlignmentSegment(text="好", start_ms=0, end_ms=400, text_start=0, text_end=1)],
            duration_seconds=0.4,
            sample_rate=sample_rate,
            is_final=True,
        )


class SlowBookTurnStreamingTTS(FakeBookTurnStreamingTTS):
    async def stream_input_synthesize(
        self,
        text_chunks,
        *,
        voice=None,
        speed_ratio=None,
        response_format=None,
        sample_rate=None,
    ):
        segment_index = len(self.calls)
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
                audio=f"book-audio-{segment_index}-{len(call['text_chunks'])}".encode(),
                mime_type="audio/L16",
                provider=self.provider_name,
                model=self.model_name,
                voice=voice or self.default_voice,
                speed_ratio=speed_ratio or 1.0,
                sequence=len(call["text_chunks"]),
                sample_rate=sample_rate,
            )
            await asyncio.sleep(10)
