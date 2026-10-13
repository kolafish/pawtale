"""Malformed model protocol must not discard a usable reply or guess an exit."""
import asyncio

import pytest

from app.domain.memoir.types import MemoirConversationAction
from app.services.memoir.voice_assistant_turn_stream import (
    AssistantTurnArtifacts, AssistantTurnStartRequest, VoiceAssistantTurnStreamSession,
)
from tests.test_memoir_voice_assistant_turn_stream import FakeStreamingAI, FakeTurnClient


@pytest.mark.ci_smoke
@pytest.mark.parametrize("prefix,body,action", [
    ("[listen]\n", "您接着讲。", "listen"),
    ("[pause]\n", "好，等您准备好再继续。", "pause"),
    ("[end]\n", "好，今天就聊到这里。", "end"),
    ("", "您接着讲。", "pause"),
    ("", "好，今天就聊到这里。", "pause"),
    ("", "老余：您接着讲。", "pause"),
])
async def test_control_boundary_preserves_speech_for_every_chunk_split(prefix, body, action):
    raw = prefix + body
    expected = body.removeprefix("老余：")
    for split in range(len(raw) + 1):
        client = FakeTurnClient()
        session = VoiceAssistantTurnStreamSession(db=None, client=client, voice_session_id="synthetic", user_id="synthetic")
        artifacts = AssistantTurnArtifacts()
        queue = asyncio.Queue()
        await session._stream_text(
            ai_client=FakeStreamingAI([raw[:split], raw[split:]], control_chunks=()),
            system_prompt="synthetic", prompt="synthetic", req=AssistantTurnStartRequest(), max_tokens=100,
            turn_id="synthetic", artifacts=artifacts, cancel_event=asyncio.Event(), text_queue=queue,
        )
        spoken = "".join([queue.get_nowait() for _ in range(queue.qsize())])
        assert spoken.strip() == expected == artifacts.text.strip()
        assert artifacts.conversation_action == MemoirConversationAction(action)
        assert "".join(event["text_delta"] for event in client.sent).strip() == expected


@pytest.mark.ci_smoke
@pytest.mark.parametrize("raw", ["[end", "[unknown]\n您好", '{"action":"end"}', "```json", ""])
async def test_incomplete_control_or_structured_output_never_becomes_speech(raw):
    client = FakeTurnClient()
    session = VoiceAssistantTurnStreamSession(db=None, client=client, voice_session_id="synthetic", user_id="synthetic")
    artifacts = AssistantTurnArtifacts()
    queue = asyncio.Queue()
    with pytest.raises(ValueError):
        await session._stream_text(
            ai_client=FakeStreamingAI(list(raw), control_chunks=()), system_prompt="synthetic", prompt="synthetic",
            req=AssistantTurnStartRequest(), max_tokens=100, turn_id="synthetic", artifacts=artifacts,
            cancel_event=asyncio.Event(), text_queue=queue,
        )
    assert queue.empty() and not client.sent and not artifacts.text


@pytest.mark.ci_smoke
async def test_reply_without_marker_completes_and_persists_pause(client, test_db, tmp_path, monkeypatch):
    from app.models.memoir import MemoirChatMessage
    from tests.memoir_helpers import create_test_voice_transcript
    from tests.test_memoir_voice_assistant_turn_stream import FakeStreamingTTS

    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    source = await create_test_voice_transcript(client, test_db, text="今天就聊到这里吧。")
    stream_client = FakeTurnClient()
    ai = FakeStreamingAI(["好，今天就聊到这里。"], control_chunks=())
    session = VoiceAssistantTurnStreamSession(
        db=test_db, client=stream_client, voice_session_id=source["session"]["id"], user_id=source["user_id"],
        ai_client_factory=lambda model=None: ai, tts_client_factory=FakeStreamingTTS,
    )
    await stream_client.push({"type": "turn.start"})
    await asyncio.wait_for(session.run(), timeout=5)
    assert not any(event["type"] == "turn.failed" for event in stream_client.sent)
    completed = next(event for event in stream_client.sent if event["type"] == "turn.completed")
    assert completed["conversation_action"] == "pause"
    assert completed["text"] == "好，今天就聊到这里。"
    assert completed["audio_file_id"]
    message = await test_db.get(MemoirChatMessage, completed["message_id"])
    assert message.content_text == completed["text"]
    assert message.metadata_json["conversation_action"] == "pause"
