from datetime import datetime, timedelta

from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirChapterSourceLinkStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
)
from app.models.memoir import (
    AudioFile,
    MemoirChapterSourceLink,
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.memoir import automation_event_metadata
from app.services.memoir import automation_events
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_tts_types import TTSResult
from tests.memoir_helpers import (
    add_test_chapter_source_links,
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    memoir_source_ref,
    post_mobile_memoir_voice_session,
)


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
    assert session["interview_type"] == "ai_interview"
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
    assert stored.interview_type == "ai_interview"

    current_user = (await client.get("/api/auth/me")).json()
    listening = await memoir_voice_sessions.mark_voice_session_listening(test_db, session["id"], current_user["id"])

    assert listening.session.status == "listening"
    await test_db.refresh(stored)
    assert stored.status == "listening"


async def test_mobile_memoir_creates_human_interview_session(client, test_db):
    response = await post_mobile_memoir_voice_session(
        client,
        json={
            "guidance_mode": "free_narration",
            "interview_type": "human_interview",
            "provider": "volc_streaming_asr",
        },
    )

    assert response.status_code == 201
    session = response.json()["session"]
    assert session["guidance_mode"] == "free_narration"
    assert session["interview_type"] == "human_interview"

    stored = await test_db.scalar(select(MemoirVoiceSession).where(MemoirVoiceSession.id == session["id"]))
    assert stored is not None
    assert stored.interview_type == "human_interview"


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


async def test_mobile_memoir_voice_no_answer_prompt_saves_assistant_message(client, test_db):
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

    response = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session['id']}/no-answer-prompt",
        json={"attempt": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["follow_up_text"] == "没关系，我先暂停一下。您准备好了，再点继续说话。"
    assert data["message"]["role"] == "assistant"
    assert data["message"]["content_text"] == data["follow_up_text"]
    assert data["message"]["turn_index"] == 1
    assert data["message"]["guidance_intent"] == "no_answer_prompt"
    assert data["primary_intent"] == "repair"
    assert data["intent_tags"] == ["repair"]
    assert data["expects_answer"] is False
    assert data["message"]["metadata_json"]["source"] == "ai.no_answer_prompt"
    assert data["message"]["metadata_json"]["prompt_version"] == "memoir_voice_no_answer_prompt_v1"
    assert data["message"]["metadata_json"]["primary_intent"] == "repair"
    assert data["message"]["metadata_json"]["intent_tags"] == ["repair"]
    assert data["message"]["metadata_json"]["expects_answer"] is False
    assert data["message"]["metadata_json"]["attempt"] == 2

    stored = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == data["message"]["id"]))
    assert stored is not None
    assert stored.content_text == "没关系，我先暂停一下。您准备好了，再点继续说话。"


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
    acknowledgement_message = MemoirChatMessage(
        project_id=guided_session["project_id"],
        voice_session_id=guided_session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text="您说的外婆家，我听到了。",
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent="acknowledge",
        turn_index=1,
        metadata_json={"source": "ai.acknowledge", "expects_answer": False},
    )
    assistant_message = MemoirChatMessage(
        project_id=guided_session["project_id"],
        voice_session_id=guided_session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text="那时候你最记得谁？",
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent="follow_up",
        turn_index=2,
        metadata_json={"source": "ai.follow_up", "expects_answer": True},
    )
    test_db.add_all([user_message, acknowledgement_message, assistant_message])
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
    empty_utterance = MemoirVoiceUtterance(
        voice_session_id=free_session["id"],
        transcript_raw="",
        transcript_edited="",
        start_ms=0,
        end_ms=1000,
        provider="test",
        provider_payload_json={},
    )
    test_db.add_all([utterance, empty_utterance])
    await test_db.commit()

    list_response = await client.get("/api/mobile/memoir/voice-sessions")

    assert list_response.status_code == 200
    list_data = list_response.json()
    assert list_data["total"] == 2
    guided_item = next(item for item in list_data["items"] if item["id"] == guided_session["id"])
    free_item = next(item for item in list_data["items"] if item["id"] == free_session["id"])
    assert guided_item["guidance_mode"] == "guided_interview"
    assert guided_item["current_guidance_module"] == "person"
    assert guided_item["user_message_count"] == 1
    assert guided_item["assistant_message_count"] == 1
    assert guided_item["utterance_count"] == 1
    assert guided_item["total_user_char_count"] == len("我小时候住在外婆家。")
    assert guided_item["total_audio_seconds"] == 2
    assert guided_item["last_message_text"] == "那时候你最记得谁？"
    assert guided_item["last_message_role"] == "assistant"
    assert free_item["utterance_count"] == 0

    effective_list_response = await client.get("/api/mobile/memoir/voice-sessions?effective_only=true")

    assert effective_list_response.status_code == 200
    effective_list = effective_list_response.json()
    assert effective_list["total"] == 1
    assert [item["id"] for item in effective_list["items"]] == [guided_session["id"]]

    progress_response = await client.get("/api/mobile/memoir/voice-progress")

    assert progress_response.status_code == 200
    progress = progress_response.json()
    assert progress["session_count"] == 1
    assert progress["active_session_count"] == 1
    assert progress["utterance_count"] == 1
    assert progress["user_message_count"] == 1
    assert progress["assistant_message_count"] == 1
    assert progress["total_user_char_count"] == len("我小时候住在外婆家。")
    assert {item["module"] for item in progress["modules"]} == {"person"}
    assert free_session["id"]


async def test_mobile_memoir_project_progress_snapshot_counts_book_state(client, test_db):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "我小时候住在外婆家。",
        guidance_mode="guided_interview",
        current_guidance_module="person",
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        "后来我去县城读书。",
        guidance_mode="guided_interview",
        current_guidance_module="period",
    )
    empty_session_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration"},
        project_id=first["session"]["project_id"],
    )
    assert empty_session_response.status_code == 201

    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=first["session"]["project_id"],
        source_ref=memoir_source_ref(first),
    )
    await create_test_chapter_draft(
        test_db,
        project_id=first["session"]["project_id"],
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
        content="外婆家的院子里有一棵树。",
    )
    await add_test_chapter_source_links(
        test_db,
        project_id=first["session"]["project_id"],
        plan_id=plan.id,
        chapter_id=chapter.id,
        source_refs=[memoir_source_ref(second, "后来我去县城读书。")],
    )
    removed_at = datetime.utcnow()
    links = (
        await test_db.execute(
            select(MemoirChapterSourceLink).where(MemoirChapterSourceLink.chapter_id == chapter.id)
        )
    ).scalars().all()
    for link in links:
        if link.utterance_id == first["utterance_id"]:
            link.merged_draft_version_id = None
        if link.utterance_id == second["utterance_id"]:
            link.status = MemoirChapterSourceLinkStatus.REMOVED.value
            link.removed_at = removed_at
        test_db.add(link)
    event = await automation_events.record_automation_event(
        test_db,
        project_id=first["session"]["project_id"],
        event_type=MemoirAutomationEventType.SOURCE_ASSIGNED,
        chapter_id=chapter.id,
        utterance_ids=[first["utterance_id"]],
        summary_text="1 条讲述已整理进外婆家的童年。",
        confidence=0.9,
    )
    event.metadata_json = {
        automation_event_metadata.VOICE_PLAYED_AT_KEY: "2026-06-23T12:00:00+00:00",
        automation_event_metadata.VOICE_PLAYED_SESSION_ID_KEY: "voice-session-1",
        automation_event_metadata.ROUTES_KEY: [
            {
                "utterance_id": first["utterance_id"],
                "source_index": 1,
                "target_chapter_id": chapter.id,
                "target_chapter_title": "外婆家的童年",
            }
        ],
    }
    await test_db.commit()

    response = await client.get(
        f"/api/mobile/memoir/projects/{first['session']['project_id']}/progress-snapshot"
    )

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["project_id"] == first["session"]["project_id"]
    assert snapshot["valid_utterance_count"] == 2
    assert snapshot["assigned_utterance_count"] == 1
    assert snapshot["pending_utterance_count"] == 1
    assert snapshot["session_count"] == 2
    assert snapshot["chapter_count"] == 1
    assert snapshot["confirmed_chapter_count"] == 1
    assert snapshot["draft_word_count"] == len("外婆家的院子里有一棵树。")
    assert snapshot["chapter_progress_items"] == [
        {
            "chapter_id": chapter.id,
                "title": "外婆家的童年",
                "sort_order": 0,
                "include_in_book": True,
                "source_count": 1,
            "pending_source_count": 1,
            "removed_source_count": 1,
            "draft_status": "confirmed",
            "draft_word_count": len("外婆家的院子里有一棵树。"),
        }
    ]
    assert snapshot["recent_automation_events"][0]["event_type"] == "source_assigned"
    assert snapshot["recent_automation_events"][0]["utterance_ids"] == [first["utterance_id"]]
    assert snapshot["recent_automation_events"][0]["summary_text"] == "1 条讲述已整理进外婆家的童年。"
    assert snapshot["recent_automation_events"][0]["metadata_json"]["voice_played_session_id"] == "voice-session-1"

    progress_response = await client.get(
        f"/api/mobile/memoir/voice-progress?project_id={first['session']['project_id']}"
    )
    assert progress_response.status_code == 200
    progress = progress_response.json()
    assert progress["utterance_count"] == 2
    assert progress["snapshot"]["assigned_utterance_count"] == 1
    assert {item["module"] for item in progress["modules"]} == {"person", "period"}

    events_response = await client.get(
        f"/api/mobile/memoir/projects/{first['session']['project_id']}/automation-events"
    )
    assert events_response.status_code == 200
    assert events_response.json()["total"] == 1
    event_item = events_response.json()["items"][0]
    event_id = event_item["id"]
    assert event_item["utterance_details"] == [
        {
            "utterance_id": first["utterance_id"],
            "source_index": 1,
            "record_title": "语音记录 1",
            "preview_text": "我小时候住在外婆家。",
            "chapter_id": chapter.id,
            "chapter_title": "外婆家的童年",
            "created_at": event_item["utterance_details"][0]["created_at"],
        }
    ]

    voice_played_response = await client.post(
        f"/api/mobile/memoir/projects/{first['session']['project_id']}/automation-events/voice-played",
        json={
            "event_ids": [event_id],
            "voice_session_id": "voice-session-2",
            "message_id": "assistant-message-2",
        },
    )
    assert voice_played_response.status_code == 200
    assert voice_played_response.json()["updated_count"] == 1

    acknowledge_response = await client.post(
        f"/api/mobile/memoir/projects/{first['session']['project_id']}/automation-events/{event_id}/acknowledge"
    )
    assert acknowledge_response.status_code == 200
    assert acknowledge_response.json()["metadata_json"]["acknowledged_by_user"] is True

    hidden_events_response = await client.get(
        f"/api/mobile/memoir/projects/{first['session']['project_id']}/automation-events"
    )
    assert hidden_events_response.status_code == 200
    assert hidden_events_response.json()["total"] == 0
    assert hidden_events_response.json()["items"] == []

    hidden_snapshot_response = await client.get(
        f"/api/mobile/memoir/projects/{first['session']['project_id']}/progress-snapshot"
    )
    assert hidden_snapshot_response.status_code == 200
    assert hidden_snapshot_response.json()["recent_automation_events"] == []


async def test_mobile_memoir_automation_events_prioritize_action_required_items(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "我小时候住在外婆家。")
    project_id = created["session"]["project_id"]
    base_time = datetime(2026, 7, 1, 12, 0, 0)
    action_events = [
        (
            MemoirAutomationEventStatus.PENDING,
            MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
            "章节初稿正在生成。",
        ),
        (
            MemoirAutomationEventStatus.NEEDS_REVIEW,
            MemoirAutomationEventType.SOURCE_HELD,
            "1 条讲述需要确认归属。",
        ),
        (
            MemoirAutomationEventStatus.FAILED,
            MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
            "章节自动补写失败，稍后可重试。",
        ),
    ]
    for index, (status, event_type, summary) in enumerate(action_events):
        event = await automation_events.record_automation_event(
            test_db,
            project_id=project_id,
            event_type=event_type,
            status=status,
            utterance_ids=[created["utterance_id"]],
            summary_text=summary,
        )
        event.created_at = base_time + timedelta(minutes=index)
        event.updated_at = event.created_at
        test_db.add(event)
    for index in range(5):
        event = await automation_events.record_automation_event(
            test_db,
            project_id=project_id,
            event_type=MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
            status=MemoirAutomationEventStatus.COMPLETED,
            utterance_ids=[created["utterance_id"]],
            summary_text=f"较新的完成事件 {index}",
        )
        event.created_at = base_time + timedelta(hours=1, minutes=index)
        event.updated_at = event.created_at
        test_db.add(event)
    await test_db.commit()

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/automation-events?limit=3")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 8
    assert [item["status"] for item in data["items"]] == ["failed", "needs_review", "pending"]
    assert [item["summary_text"] for item in data["items"]] == [
        "章节自动补写失败，稍后可重试。",
        "1 条讲述需要确认归属。",
        "章节初稿正在生成。",
    ]


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
    assert response.json()["detail"] == "只有 AI 回应可以生成语音"


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
