from sqlalchemy import select

from app.models.memoir import MemoirVoiceSession
from app.services.memoir import voice_sessions as memoir_voice_sessions


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
