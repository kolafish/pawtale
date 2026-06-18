from sqlalchemy import select

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole
from app.models.memoir import AudioFile, MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_tts import TTSResult
from tests.memoir_helpers import post_mobile_memoir_voice_session


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
    default_voice = ""

    def __init__(self, audio_results=None, alignment_results=None):
        self.calls = []
        self.audio_results = list(audio_results or [b"fake-mp3-audio"])
        self.alignment_results = list(alignment_results or [])

    async def synthesize(self, text, *, voice=None, speed_ratio=None):
        call = {"text": text, "voice": voice}
        if speed_ratio is not None:
            call["speed_ratio"] = speed_ratio
        self.calls.append(call)
        audio = self.audio_results.pop(0) if self.audio_results else b"fake-mp3-audio"
        alignment = self.alignment_results.pop(0) if self.alignment_results else []
        duration_seconds = max((segment.end_ms for segment in alignment), default=0) / 1000 if alignment else None
        return TTSResult(
            audio=audio,
            mime_type="audio/mpeg",
            extension=".mp3",
            provider=self.provider_name,
            model=self.model_name,
            voice=voice or self.default_voice,
            speed_ratio=speed_ratio or 1.0,
            alignment=alignment,
            duration_seconds=duration_seconds,
        )


async def test_mobile_memoir_voice_session_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )

    assert response.status_code == 401


async def test_mobile_memoir_creates_voice_session_for_existing_project(client, test_db):
    response = await post_mobile_memoir_voice_session(
        client,
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
    response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "scripted_questions"},
    )

    assert response.status_code == 422


async def test_mobile_memoir_rejects_direct_message_and_utterance_writes(client):
    create_response = await post_mobile_memoir_voice_session(
        client,
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
    create_response = await post_mobile_memoir_voice_session(
        client,
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

    create_failed_response = await post_mobile_memoir_voice_session(
        client,
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
    create_response = await post_mobile_memoir_voice_session(
        client,
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
    assert data["message"]["metadata_json"]["prompt_version"] == "memoir_voice_followup_v3"
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
    create_response = await post_mobile_memoir_voice_session(
        client,
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
    assert "主题讲述首次开场" in fake_ai.calls[0]["user_prompt"]
    assert "第一次主题讲述开场" in fake_ai.calls[0]["user_prompt"]
    assert "今天可以围绕不同主题讲回忆" in fake_ai.calls[0]["user_prompt"]
    assert "要明确介绍可聊主题" in fake_ai.calls[0]["user_prompt"]
    assert "人物、事情、家庭、工作、地点、某一天、人生阶段、闲聊" in fake_ai.calls[0]["user_prompt"]
    assert "聊一个最挂念的家人" in fake_ai.calls[0]["user_prompt"]
    assert "聊第一份工作" in fake_ai.calls[0]["user_prompt"]
    assert "暂无对话" in fake_ai.calls[0]["user_prompt"]
    assert "暂无历史会话" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_voice_follow_up_starts_free_session_without_user_message(
    client,
    monkeypatch,
):
    create_response = await post_mobile_memoir_voice_session(
        client,
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
    assert "从小时候或最早的记忆开始讲起" in fake_ai.calls[0]["user_prompt"]
    assert "暂无对话" in fake_ai.calls[0]["user_prompt"]
    assert "暂无历史会话" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_guided_opening_references_previous_session(
    client,
    test_db,
    monkeypatch,
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
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "event"},
    )
    session = create_response.json()["session"]
    fake_ai = FakeFollowUpAI("你好，我是老余。我们可以随便聊些主题，比如人物、事情或家庭；上次讲到你第一次去上海找工作，今天想接着聊，还是重新选个主题？")
    monkeypatch.setattr(
        "app.services.memoir.voice_followups.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/follow-up",
        json={},
    )

    assert response.status_code == 200
    prompt = fake_ai.calls[0]["user_prompt"]
    assert "主题讲述继续开场" in prompt
    assert "叙述者之前已有同模式讲述记录" in prompt
    assert "欢迎回来，咱们接着聊" in prompt
    assert "不要重复完整自我介绍" in prompt
    assert "优先邀请叙述者接着上次的话题继续讲" in prompt
    assert "主题介绍只做一句简短兜底" in prompt
    assert "不要完整展开主题菜单" in prompt
    assert "叙述者：上次讲到我第一次去上海找工作。" in prompt


async def test_mobile_memoir_guided_follow_up_can_continue_previous_session(
    client,
    test_db,
    monkeypatch,
):
    previous_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "work"},
    )
    previous_session = previous_response.json()["session"]
    previous_message = MemoirChatMessage(
        project_id=previous_session["project_id"],
        voice_session_id=previous_session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text="我讲到进厂第一天跟着师傅学开机器。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    test_db.add(previous_message)
    await test_db.commit()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "work"},
    )
    session = create_response.json()["session"]
    current_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text="我们接着上次讲吧。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    test_db.add(current_message)
    await test_db.commit()
    fake_ai = FakeFollowUpAI("那进厂第一天，师傅先教了你什么？")
    monkeypatch.setattr(
        "app.services.memoir.voice_followups.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/follow-up",
        json={},
    )

    assert response.status_code == 200
    prompt = fake_ai.calls[0]["user_prompt"]
    assert "如果叙述者表达想接着上次聊" in prompt
    assert "叙述者：我们接着上次讲吧。" in prompt
    assert "叙述者：我讲到进厂第一天跟着师傅学开机器。" in prompt


async def test_mobile_memoir_free_opening_prompts_previous_timeline_continuation(
    client,
    test_db,
    monkeypatch,
):
    previous_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    previous_session = previous_response.json()["session"]
    previous_message = MemoirChatMessage(
        project_id=previous_session["project_id"],
        voice_session_id=previous_session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text="我小时候讲到在外婆家门口等父亲回来。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    test_db.add(previous_message)
    await test_db.commit()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
    )
    session = create_response.json()["session"]
    fake_ai = FakeFollowUpAI("你好，我是老余。上次你讲到小时候在外婆家门口等父亲回来，我们可以从那里接着讲。")
    monkeypatch.setattr(
        "app.services.memoir.voice_followups.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/follow-up",
        json={},
    )

    assert response.status_code == 200
    prompt = fake_ai.calls[0]["user_prompt"]
    assert "叙述者之前已有讲述记录" in prompt
    assert "提醒上次讲到哪里" in prompt
    assert "可以从上次停下来的地方接着讲" in prompt
    assert "叙述者：我小时候讲到在外婆家门口等父亲回来。" in prompt


async def test_mobile_memoir_voice_session_list_and_progress(client, test_db):
    guided_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "person"},
    )
    free_response = await post_mobile_memoir_voice_session(
        client,
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
    create_response = await post_mobile_memoir_voice_session(
        client,
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
    fake_tts = FakeTTS(
        alignment_results=[
            [
                TTSAlignmentSegment(
                    text="你",
                    start_ms=0,
                    end_ms=120,
                    text_start=0,
                    text_end=1,
                ),
                TTSAlignmentSegment(
                    text="想",
                    start_ms=120,
                    end_ms=240,
                    text_start=1,
                    text_end=2,
                ),
            ]
        ]
    )

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
    assert data["duration_seconds"] == 0.24
    assert data["alignment_provider"] == "fake-tts"
    assert data["alignment"][:2] == [
        {
            "text": "你",
            "start_ms": 0,
            "end_ms": 120,
            "text_start": 0,
            "text_end": 1,
            "confidence": None,
        },
        {
            "text": "想",
            "start_ms": 120,
            "end_ms": 240,
            "text_start": 1,
            "text_end": 2,
            "confidence": None,
        },
    ]
    assert data["message"]["audio_file_id"] == data["audio_file_id"]
    assert fake_tts.calls == [{"text": "你想先聊哪一位亲人？", "voice": "warm"}]

    await test_db.refresh(assistant_message)
    assert assistant_message.audio_file_id == data["audio_file_id"]
    assert assistant_message.metadata_json["tts_provider"] == "fake-tts"
    assert assistant_message.metadata_json["tts_timing_source"] == "fake-tts"
    assert assistant_message.metadata_json["tts_alignment"][0]["text"] == "你"
    playback = await client.get(f"/api/mobile/memoir/audio-files/{data['audio_file_id']}/playback")
    assert playback.status_code == 200
    assert playback.content == b"fake-mp3-audio"


async def test_mobile_memoir_assistant_tts_force_regenerate_deletes_replaced_audio(
    client,
    test_db,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview"},
    )
    session = create_response.json()["session"]
    assistant_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text="继续讲讲那一次分别。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        guidance_intent="follow_up",
        metadata_json={"source": "test"},
    )
    test_db.add(assistant_message)
    await test_db.commit()
    await test_db.refresh(assistant_message)
    fake_tts = FakeTTS([b"first-mp3-audio", b"second-mp3-audio"])

    monkeypatch.setattr("app.services.memoir.voice_tts.get_tts_client", lambda: fake_tts)

    first_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={},
    )
    assert first_response.status_code == 200
    first_audio_id = first_response.json()["audio_file_id"]
    first_audio = await test_db.scalar(select(AudioFile).where(AudioFile.id == first_audio_id))
    assert first_audio is not None
    first_audio_path = tmp_path / "audio" / first_audio.user_id / f"{first_audio.id}.mp3"
    assert first_audio_path.read_bytes() == b"first-mp3-audio"

    second_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={"force_regenerate": True},
    )

    assert second_response.status_code == 200
    second_audio_id = second_response.json()["audio_file_id"]
    assert second_audio_id != first_audio_id
    assert await test_db.scalar(select(AudioFile).where(AudioFile.id == first_audio_id)) is None
    assert not first_audio_path.exists()
    assert fake_tts.calls == [
        {"text": "继续讲讲那一次分别。", "voice": None},
        {"text": "继续讲讲那一次分别。", "voice": None},
    ]

    second_playback = await client.get(f"/api/mobile/memoir/audio-files/{second_audio_id}/playback")
    assert second_playback.status_code == 200
    assert second_playback.content == b"second-mp3-audio"


async def test_mobile_memoir_assistant_tts_regenerates_stale_provider_cache(
    client,
    test_db,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview"},
    )
    session = create_response.json()["session"]
    assistant_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text="说说你最记得的那一天。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        guidance_intent="follow_up",
        metadata_json={"source": "test"},
    )
    test_db.add(assistant_message)
    await test_db.commit()
    await test_db.refresh(assistant_message)

    fake_tts = FakeTTS([b"old-mp3-audio", b"new-mp3-audio"])

    monkeypatch.setattr("app.services.memoir.voice_tts.get_tts_client", lambda: fake_tts)

    first_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={},
    )
    assert first_response.status_code == 200
    first_audio_id = first_response.json()["audio_file_id"]
    first_audio = await test_db.scalar(select(AudioFile).where(AudioFile.id == first_audio_id))
    assert first_audio is not None
    first_audio_path = tmp_path / "audio" / first_audio.user_id / f"{first_audio.id}.mp3"
    assert first_audio_path.read_bytes() == b"old-mp3-audio"
    assert first_response.json()["alignment"] == []
    assert first_response.json()["message"]["metadata_json"]["tts_provider"] == "fake-tts"

    await test_db.refresh(assistant_message)
    assistant_message.metadata_json = {
        **assistant_message.metadata_json,
        "tts_provider": "stepfun",
        "tts_model": "step-tts-2",
    }
    test_db.add(assistant_message)
    await test_db.commit()

    second_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={},
    )

    assert second_response.status_code == 200
    second_data = second_response.json()
    assert second_data["audio_file_id"] != first_audio_id
    assert second_data["provider"] == "fake-tts"
    assert second_data["message"]["metadata_json"]["tts_provider"] == "fake-tts"
    assert await test_db.scalar(select(AudioFile).where(AudioFile.id == first_audio_id)) is None
    assert not first_audio_path.exists()
    assert fake_tts.calls == [
        {"text": "说说你最记得的那一天。", "voice": None},
        {"text": "说说你最记得的那一天。", "voice": None},
    ]

    second_playback = await client.get(f"/api/mobile/memoir/audio-files/{second_data['audio_file_id']}/playback")
    assert second_playback.status_code == 200
    assert second_playback.content == b"new-mp3-audio"


async def test_mobile_memoir_assistant_tts_reuses_cache_after_timing_attempt_without_segments(
    client,
    test_db,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview"},
    )
    session = create_response.json()["session"]
    assistant_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text="再讲讲那时候的细节。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        guidance_intent="follow_up",
        metadata_json={"source": "test"},
    )
    test_db.add(assistant_message)
    await test_db.commit()
    await test_db.refresh(assistant_message)

    fake_tts = FakeTTS([b"first-mp3-audio", b"second-mp3-audio"])

    monkeypatch.setattr("app.services.memoir.voice_tts.get_tts_client", lambda: fake_tts)

    first_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={},
    )
    assert first_response.status_code == 200
    first_data = first_response.json()
    assert first_data["alignment"] == []
    assert first_data["message"]["metadata_json"]["tts_timing_source"] is None

    second_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={},
    )

    assert second_response.status_code == 200
    second_data = second_response.json()
    assert second_data["audio_file_id"] == first_data["audio_file_id"]
    assert fake_tts.calls == [{"text": "再讲讲那时候的细节。", "voice": None}]

    playback = await client.get(f"/api/mobile/memoir/audio-files/{first_data['audio_file_id']}/playback")
    assert playback.status_code == 200
    assert playback.content == b"first-mp3-audio"


async def test_mobile_memoir_assistant_tts_regenerates_cache_when_speed_changes(
    client,
    test_db,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview"},
    )
    session = create_response.json()["session"]
    assistant_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text="慢一点，再讲讲细节。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        guidance_intent="follow_up",
        metadata_json={"source": "test"},
    )
    test_db.add(assistant_message)
    await test_db.commit()
    await test_db.refresh(assistant_message)

    fake_tts = FakeTTS([b"slow-mp3-audio", b"fast-mp3-audio"])
    monkeypatch.setattr("app.services.memoir.voice_tts.get_tts_client", lambda: fake_tts)

    first_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={"speed_ratio": 0.7},
    )
    assert first_response.status_code == 200
    first_data = first_response.json()
    first_audio_id = first_data["audio_file_id"]
    first_audio = await test_db.scalar(select(AudioFile).where(AudioFile.id == first_audio_id))
    assert first_audio is not None
    first_audio_path = tmp_path / "audio" / first_audio.user_id / f"{first_audio.id}.mp3"
    assert first_audio_path.read_bytes() == b"slow-mp3-audio"
    assert first_data["message"]["metadata_json"]["tts_speed_ratio"] == 0.7

    second_response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/messages/{assistant_message.id}/tts",
        json={"speed_ratio": 1.5},
    )

    assert second_response.status_code == 200
    second_data = second_response.json()
    assert second_data["audio_file_id"] != first_audio_id
    assert second_data["message"]["metadata_json"]["tts_speed_ratio"] == 1.5
    assert await test_db.scalar(select(AudioFile).where(AudioFile.id == first_audio_id)) is None
    assert not first_audio_path.exists()
    assert fake_tts.calls == [
        {"text": "慢一点，再讲讲细节。", "voice": None, "speed_ratio": 0.7},
        {"text": "慢一点，再讲讲细节。", "voice": None, "speed_ratio": 1.5},
    ]

    playback = await client.get(f"/api/mobile/memoir/audio-files/{second_data['audio_file_id']}/playback")
    assert playback.status_code == 200
    assert playback.content == b"fast-mp3-audio"


async def test_mobile_memoir_assistant_tts_rejects_user_message(client, test_db):
    create_response = await post_mobile_memoir_voice_session(
        client,
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
    create_response = await post_mobile_memoir_voice_session(
        anonymous_client,
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
    create_response = await post_mobile_memoir_voice_session(
        anonymous_client,
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
