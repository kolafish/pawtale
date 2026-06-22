from tests.memoir_helpers import post_mobile_memoir_voice_session


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
