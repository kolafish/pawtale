from tests.memoir_helpers import post_mobile_memoir_voice_session
from app.services.memoir.voice_authorization import (
    CURRENT_VOICE_AUTHORIZATION_VERSION,
    audio_authorizer,
    has_current_voice_authorization_consent,
)
from app.services.memoir import voice_authorization


async def test_recording_requires_current_user_agreement(client, test_db, monkeypatch):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    assert not await has_current_voice_authorization_consent(test_db, user_id)
    monkeypatch.setattr(voice_authorization, "AUDIO_AUTHORIZATION_REFRESH_SECONDS", 0)
    authorized_audio = audio_authorizer(test_db, user_id)
    assert not await authorized_audio()

    for agreed in (True, False):
        response = await client.post(
            "/api/mobile/memoir/voice-authorization-consent",
            json={"version": CURRENT_VOICE_AUTHORIZATION_VERSION, "agreed": agreed},
        )
        assert response.status_code == 201
        assert await has_current_voice_authorization_consent(test_db, user_id) is agreed
        assert await authorized_audio() is agreed


async def test_voice_consent_is_not_shared_between_accounts(anonymous_client, test_db):
    first = await anonymous_client.post(
        "/api/auth/register",
        json={"email": "first-voice@example.com", "password": "password123"},
    )
    assert first.status_code == 201
    assert (await anonymous_client.post(
        "/api/mobile/memoir/voice-authorization-consent",
        json={"version": CURRENT_VOICE_AUTHORIZATION_VERSION, "agreed": True},
    )).status_code == 201
    assert await has_current_voice_authorization_consent(test_db, first.json()["user"]["id"])

    await anonymous_client.post("/api/auth/logout")
    second = await anonymous_client.post(
        "/api/auth/register",
        json={"email": "second-voice@example.com", "password": "password123"},
    )
    assert second.status_code == 201
    assert not await has_current_voice_authorization_consent(test_db, second.json()["user"]["id"])


async def test_voice_authorization_consent_requires_login(anonymous_client):
    get_response = await anonymous_client.get(
        "/api/mobile/memoir/voice-authorization-consent",
        params={"version": "2026-06-18"},
    )
    post_response = await anonymous_client.post(
        "/api/mobile/memoir/voice-authorization-consent",
        json={
            "version": "2026-06-18",
            "agreed": True,
        },
    )

    assert get_response.status_code == 401
    assert post_response.status_code == 401


async def test_voice_authorization_consent_records_current_version(client):
    agreed_response = await client.post(
        "/api/mobile/memoir/voice-authorization-consent",
        json={
            "version": "2026-06-18",
            "agreed": True,
            "source": "miniprogram",
            "metadata_json": {"agreed_at": "2026-06-18T00:00:00.000Z"},
        },
    )
    assert agreed_response.status_code == 201
    agreed = agreed_response.json()
    assert agreed["consent_type"] == "voice_authorization"
    assert agreed["agreed"] is True
    assert agreed["metadata_json"]["agreed_at"] == "2026-06-18T00:00:00.000Z"

    status_response = await client.get(
        "/api/mobile/memoir/voice-authorization-consent",
        params={"version": "2026-06-18"},
    )
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["has_current_consent"] is True
    assert status["consent"]["id"] == agreed["id"]

    disagreed_response = await client.post(
        "/api/mobile/memoir/voice-authorization-consent",
        json={
            "version": "2026-06-18",
            "agreed": False,
            "source": "miniprogram",
        },
    )
    assert disagreed_response.status_code == 201

    status_response = await client.get(
        "/api/mobile/memoir/voice-authorization-consent",
        params={"version": "2026-06-18"},
    )
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["has_current_consent"] is False
    assert status["consent"]["agreed"] is False


async def test_voice_authorization_consent_validates_source_session_owner(client):
    session_response = await post_mobile_memoir_voice_session(
        client,
        {
            "guidance_mode": "free_narration",
            "metadata_json": {"entry": "authorization_test"},
        },
    )
    assert session_response.status_code == 201
    voice_session_id = session_response.json()["session"]["id"]

    response = await client.post(
        "/api/mobile/memoir/voice-authorization-consent",
        json={
            "version": "2026-06-18",
            "agreed": True,
            "source": "miniprogram",
            "source_session_id": voice_session_id,
        },
    )
    assert response.status_code == 201
    assert response.json()["source_session_id"] == voice_session_id

    missing_response = await client.post(
        "/api/mobile/memoir/voice-authorization-consent",
        json={
            "version": "2026-06-18",
            "agreed": True,
            "source": "miniprogram",
            "source_session_id": "missing-session",
        },
    )
    assert missing_response.status_code == 404
