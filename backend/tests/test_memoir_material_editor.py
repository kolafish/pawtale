from datetime import datetime

from sqlalchemy import select

from app.models.memoir import MemoirTranscriptVersion, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.users import User
from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project


async def test_web_materials_follow_interview_time_and_manual_correction_state(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="七月六日讲到小时候的院子。",
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="七月七日讲到第一次去县城。",
    )
    first_session = await test_db.get(MemoirVoiceSession, first["session"]["id"])
    second_session = await test_db.get(MemoirVoiceSession, second["session"]["id"])
    first_session.started_at = datetime(2026, 7, 6, 9, 0)
    second_session.started_at = datetime(2026, 7, 7, 9, 0)
    await test_db.commit()

    response = await client.get(f"/api/memoir/projects/{project_id}/materials")

    assert response.status_code == 200
    data = response.json()
    assert [item["id"] for item in data["items"]] == [first["utterance_id"], second["utterance_id"]]
    assert data["items"][0]["session_started_at"].startswith("2026-07-06T09:00:00")
    assert all(item["has_manual_correction"] is False for item in data["items"])

    updated = await client.patch(
        f"/api/memoir/projects/{project_id}/materials/{second['utterance_id']}/transcript",
        json={"transcript_edited": "七月七日，我第一次去县城。"},
    )
    corrected = await client.get(
        f"/api/memoir/projects/{project_id}/materials",
        params={"correction_status": "corrected"},
    )
    session_filtered = await client.get(
        f"/api/memoir/projects/{project_id}/materials",
        params={"voice_session_id": first["session"]["id"]},
    )

    assert updated.status_code == 200
    assert updated.json()["transcript_raw"] == "七月七日讲到第一次去县城。"
    assert updated.json()["has_manual_correction"] is True
    assert updated.json()["manual_corrected_at"] is not None
    assert corrected.status_code == 200
    assert [item["id"] for item in corrected.json()["items"]] == [second["utterance_id"]]
    assert session_filtered.status_code == 200
    assert [item["id"] for item in session_filtered.json()["items"]] == [first["utterance_id"]]

    stored = await test_db.get(MemoirVoiceUtterance, second["utterance_id"])
    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.source_artifact_id == second["utterance_id"]
            )
        )
    ).scalars().all()
    assert stored.transcript_raw == "七月七日讲到第一次去县城。"
    assert {version.version_type for version in versions} == {"asr_raw", "user_edited"}


async def test_web_materials_allow_admin_without_weakening_project_scope(anonymous_client, test_db):
    owner_response = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "material-owner@example.com",
            "password": "password123",
            "display_name": "素材所有者",
        },
    )
    assert owner_response.status_code == 201
    first_project_id = await ensure_mobile_memoir_project(anonymous_client, "第一本回忆录")
    second_project_response = await anonymous_client.post(
        "/api/mobile/memoir/projects",
        json={"title": "第二本回忆录"},
    )
    assert second_project_response.status_code == 201
    second_project_id = second_project_response.json()["id"]
    first = await create_test_voice_transcript(
        anonymous_client,
        test_db,
        project_id=first_project_id,
        text="第一本书的素材。",
    )
    second = await create_test_voice_transcript(
        anonymous_client,
        test_db,
        project_id=second_project_id,
        text="第二本书的素材。",
    )

    await anonymous_client.post("/api/auth/logout")
    other_response = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "material-other@example.com",
            "password": "password123",
            "display_name": "其他用户",
        },
    )
    assert other_response.status_code == 201
    denied = await anonymous_client.get(f"/api/memoir/projects/{first_project_id}/materials")
    assert denied.status_code == 404

    await anonymous_client.post("/api/auth/logout")
    admin_response = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "material-admin@example.com",
            "password": "password123",
            "display_name": "素材管理员",
        },
    )
    assert admin_response.status_code == 201
    admin = await test_db.scalar(select(User).where(User.email == "material-admin@example.com"))
    admin.role = "admin"
    await test_db.commit()

    listed = await anonymous_client.get(f"/api/memoir/projects/{first_project_id}/materials")
    mismatched = await anonymous_client.patch(
        f"/api/memoir/projects/{first_project_id}/materials/{second['utterance_id']}/transcript",
        json={"transcript_edited": "不应跨项目修改"},
    )
    updated = await anonymous_client.patch(
        f"/api/memoir/projects/{first_project_id}/materials/{first['utterance_id']}/transcript",
        json={"transcript_edited": "管理员校正后的第一本书素材。"},
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [first["utterance_id"]]
    assert mismatched.status_code == 404
    assert updated.status_code == 200
    assert updated.json()["display_text"] == "管理员校正后的第一本书素材。"
