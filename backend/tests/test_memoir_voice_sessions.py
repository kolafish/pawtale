from sqlalchemy import select

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir.voice_tts import TTSResult


class FakeFollowUpAI:
    provider_name = "fake-ai"
    model_name = "fake-follow-up"

    def __init__(self, text="那时候你最记得谁？"):
        self.text = text
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return self.text


class FakeTTS:
    provider_name = "fake-tts"
    model_name = "fake-tts-model"

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, *, voice=None):
        self.calls.append({"text": text, "voice": voice})
        return TTSResult(
            audio=b"fake-mp3-audio",
            mime_type="audio/mpeg",
            extension=".mp3",
            provider=self.provider_name,
            model=self.model_name,
        )


async def test_mobile_memoir_voice_session_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )

    assert response.status_code == 401


async def test_mobile_memoir_creates_voice_session_for_default_project(client, test_db):
    response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={
            "guidance_mode": "guided_interview",
            "current_guidance_module": "person",
            "provider": "volc_streaming_asr",
            "input_format": "pcm_s16le_16000_mono",
            "metadata_json": {"narrator_id": "elder_1"},
        },
    )

    assert response.status_code == 201
    data = response.json()
    session = data["session"]
    assert session["status"] == "created"
    assert session["guidance_mode"] == "guided_interview"
    assert session["current_guidance_module"] == "person"
    assert session["provider"] == "volc_streaming_asr"
    assert session["input_format"] == "pcm_s16le_16000_mono"
    assert session["metadata_json"] == {"narrator_id": "elder_1"}
    assert session["started_at"] is not None
    assert data["messages"] == []
    assert data["utterances"] == []

    stored = await test_db.scalar(select(MemoirVoiceSession).where(MemoirVoiceSession.id == session["id"]))
    assert stored is not None
    assert stored.guidance_mode == "guided_interview"

    current_user = (await client.get("/api/auth/me")).json()
    listening = await memoir_voice_sessions.mark_voice_session_listening(test_db, session["id"], current_user["id"])

    assert listening.session.status == "listening"
    await test_db.refresh(stored)
    assert stored.status == "listening"


async def test_mobile_memoir_voice_session_rejects_invalid_guidance_mode(client):
    response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "scripted_questions"},
    )

    assert response.status_code == 422


async def test_mobile_memoir_rejects_direct_message_and_utterance_writes(client):
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]

    message_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{voice_session_id}/messages",
        json={
            "role": "user",
            "content_text": "我小时候住在外婆家。",
            "content_status": "final",
        },
    )
    utterance_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{voice_session_id}/utterances",
        json={
            "transcript_raw": "我小时候住在外婆家。",
            "start_ms": 100,
            "end_ms": 2400,
        },
    )

    assert message_response.status_code == 404
    assert utterance_response.status_code == 404


async def test_mobile_memoir_voice_session_stop_and_failure_states(client):
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]

    stop_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{voice_session_id}/stop",
        json={},
    )

    assert stop_response.status_code == 200
    stopped = stop_response.json()["session"]
    assert stopped["status"] == "stopped"
    assert stopped["stopped_at"] is not None

    create_failed_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    failed_session_id = create_failed_response.json()["session"]["id"]
    failed_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{failed_session_id}/stop",
        json={"error_message": "provider disconnected"},
    )

    failed = failed_response.json()["session"]
    assert failed["status"] == "failed"
    assert failed["error_message"] == "provider disconnected"


async def test_mobile_memoir_voice_follow_up_generates_and_saves_assistant_message(
    client,
    test_db,
    monkeypatch,
):
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    session = create_response.json()["session"]
    user_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text="我小时候住在外婆家。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    test_db.add(user_message)
    await test_db.commit()
    fake_ai = FakeFollowUpAI()
    monkeypatch.setattr(
        "app.services.memoir.voice_followups.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/follow-up",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["follow_up_text"] == "那时候你最记得谁？"
    assert data["message"]["role"] == "assistant"
    assert data["message"]["content_text"] == "那时候你最记得谁？"
    assert data["message"]["turn_index"] == 1
    assert data["message"]["guidance_intent"] == "follow_up"
    assert data["message"]["metadata_json"]["prompt_version"] == "memoir_voice_followup_v2"
    assert data["message"]["metadata_json"]["has_user_message"] is True
    assert "老余" in fake_ai.calls[0]["system_prompt"]
    assert "我小时候住在外婆家。" in fake_ai.calls[0]["user_prompt"]
    assert "不要一直纠缠同一件事的不同细节" in fake_ai.calls[0]["user_prompt"]

    stored = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == data["message"]["id"]))
    assert stored is not None
    assert stored.content_text == "那时候你最记得谁？"


async def test_mobile_memoir_voice_follow_up_starts_guided_session_without_user_message(
    client,
    monkeypatch,
):
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "guided_interview", "current_guidance_module": "person"},
    )
    session = create_response.json()["session"]
    fake_ai = FakeFollowUpAI("你想先聊哪一位亲人？")
    monkeypatch.setattr(
        "app.services.memoir.voice_followups.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/follow-up",
        json={"regenerate": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["message"]["turn_index"] == 0
    assert data["message"]["guidance_intent"] == "opening"
    assert data["message"]["metadata_json"]["source"] == "ai.opening"
    assert data["message"]["metadata_json"]["regenerate"] is True
    assert data["message"]["metadata_json"]["has_user_message"] is False
    assert "老余" in fake_ai.calls[0]["system_prompt"]
    assert "人物、事情、家庭、工作、地点、某一天、人生阶段、闲聊" in fake_ai.calls[0]["user_prompt"]
    assert "聊一个最挂念的家人" in fake_ai.calls[0]["user_prompt"]
    assert "聊第一份工作" in fake_ai.calls[0]["user_prompt"]
    assert "暂无对话" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_voice_follow_up_starts_free_session_without_user_message(
    client,
    monkeypatch,
):
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    session = create_response.json()["session"]
    fake_ai = FakeFollowUpAI("您想从哪段回忆开始讲起？")
    monkeypatch.setattr(
        "app.services.memoir.voice_followups.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/follow-up",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["follow_up_text"] == "您想从哪段回忆开始讲起？"
    assert data["message"]["turn_index"] == 0
    assert data["message"]["guidance_intent"] == "opening"
    assert data["message"]["metadata_json"]["source"] == "ai.opening"
    assert data["message"]["metadata_json"]["has_user_message"] is False
    assert "老余" in fake_ai.calls[0]["system_prompt"]
    assert "自由讲述开场" in fake_ai.calls[0]["user_prompt"]
    assert "用“老余”做简短自我介绍" in fake_ai.calls[0]["user_prompt"]
    assert "不要指定主题" in fake_ai.calls[0]["user_prompt"]
    assert "暂无对话" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_voice_session_list_and_progress(client, test_db):
    guided_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "guided_interview", "current_guidance_module": "person"},
    )
    free_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    guided_session = guided_response.json()["session"]
    free_session = free_response.json()["session"]
    user_message = MemoirChatMessage(
        project_id=guided_session["project_id"],
        voice_session_id=guided_session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text="我小时候住在外婆家。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    assistant_message = MemoirChatMessage(
        project_id=guided_session["project_id"],
        voice_session_id=guided_session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text="那时候你最记得谁？",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=1,
        metadata_json={"source": "test"},
    )
    test_db.add_all([user_message, assistant_message])
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=guided_session["id"],
        chat_message_id=user_message.id,
        transcript_raw=user_message.content_text,
        transcript_edited="",
        start_ms=0,
        end_ms=2500,
        provider="test",
        provider_payload_json={},
    )
    test_db.add(utterance)
    await test_db.commit()

    list_response = await client.get("/api/mobile/memoir/voice-sessions")

    assert list_response.status_code == 200
    list_data = list_response.json()
    assert list_data["total"] == 2
    guided_item = next(item for item in list_data["items"] if item["id"] == guided_session["id"])
    assert guided_item["guidance_mode"] == "guided_interview"
    assert guided_item["current_guidance_module"] == "person"
    assert guided_item["user_message_count"] == 1
    assert guided_item["assistant_message_count"] == 1
    assert guided_item["utterance_count"] == 1
    assert guided_item["total_user_char_count"] == len("我小时候住在外婆家。")
    assert guided_item["total_audio_seconds"] == 2
    assert guided_item["last_message_text"] == "那时候你最记得谁？"
    assert guided_item["last_message_role"] == "assistant"

    progress_response = await client.get("/api/mobile/memoir/voice-progress")

    assert progress_response.status_code == 200
    progress = progress_response.json()
    assert progress["session_count"] == 2
    assert progress["active_session_count"] == 2
    assert progress["utterance_count"] == 1
    assert progress["user_message_count"] == 1
    assert progress["assistant_message_count"] == 1
    assert progress["total_user_char_count"] == len("我小时候住在外婆家。")
    assert {item["module"] for item in progress["modules"]} == {"person", "free_narration"}
    assert free_session["id"]


async def test_mobile_memoir_assistant_tts_generates_audio_for_follow_up_message(
    client,
    test_db,
    monkeypatch,
):
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "guided_interview"},
    )
    session = create_response.json()["session"]
    assistant_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text="你想先聊哪一位亲人？",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        guidance_intent="follow_up",
        metadata_json={"source": "test"},
    )
    test_db.add(assistant_message)
    await test_db.commit()
    await test_db.refresh(assistant_message)
    fake_tts = FakeTTS()
    monkeypatch.setattr("app.services.memoir.voice_tts.get_tts_client", lambda: fake_tts)

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={"voice": "warm"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["audio_file_id"]
    assert data["provider"] == "fake-tts"
    assert data["model"] == "fake-tts-model"
    assert data["mime_type"] == "audio/mpeg"
    assert data["message"]["audio_file_id"] == data["audio_file_id"]
    assert fake_tts.calls == [{"text": "你想先聊哪一位亲人？", "voice": "warm"}]

    await test_db.refresh(assistant_message)
    assert assistant_message.audio_file_id == data["audio_file_id"]
    assert assistant_message.metadata_json["tts_provider"] == "fake-tts"
    playback = await client.get(f"/api/mobile/memoir/audio-files/{data['audio_file_id']}/playback")
    assert playback.status_code == 200
    assert playback.content == b"fake-mp3-audio"


async def test_mobile_memoir_assistant_tts_rejects_user_message(client, test_db):
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    session = create_response.json()["session"]
    user_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text="我小时候住在外婆家。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    test_db.add(user_message)
    await test_db.commit()
    await test_db.refresh(user_message)

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{user_message.id}/tts",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "只有 AI 追问可以生成语音"


async def test_mobile_memoir_voice_sessions_are_user_scoped(anonymous_client):
    first_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "voice-first@example.com",
            "password": "password123",
            "display_name": "第一位作者",
        },
    )
    assert first_register.status_code == 201
    create_response = await anonymous_client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]

    await anonymous_client.post("/api/auth/logout")
    second_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "voice-second@example.com",
            "password": "password123",
            "display_name": "第二位作者",
        },
    )
    assert second_register.status_code == 201

    response = await anonymous_client.get(f"/api/mobile/memoir/voice-sessions/{voice_session_id}")

    assert response.status_code == 404


async def test_mobile_memoir_voice_follow_up_is_user_scoped(anonymous_client):
    first_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "voice-follow-first@example.com",
            "password": "password123",
            "display_name": "第一位作者",
        },
    )
    assert first_register.status_code == 201
    create_response = await anonymous_client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = create_response.json()["session"]["id"]

    await anonymous_client.post("/api/auth/logout")
    second_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "voice-follow-second@example.com",
            "password": "password123",
            "display_name": "第二位作者",
        },
    )
    assert second_register.status_code == 201

    response = await anonymous_client.post(
        f"/api/mobile/memoir/voice-sessions/{voice_session_id}/follow-up",
        json={},
    )

    assert response.status_code == 404
