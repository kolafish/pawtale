from __future__ import annotations

from sqlalchemy import select
from app.models.audit import AuditDailyFeatureUsage, AuditDailyUserUsage, AuditEvent, AuditSession
from app.models.users import User


def audit_payload(client_session_id: str = "client-session-1") -> dict:
    return {
        "client_session_id": client_session_id,
        "platform": "miniprogram",
        "app_version": "1.0.0",
        "device_summary": "iPhone",
        "events": [
            {
                "client_event_id": "event-1",
                "event_type": "page_view",
                "occurred_at": "2026-07-01T10:00:00+08:00",
                "event_date": "2026-07-01",
                "page_path": "/pages/voice-chat/index",
                "feature_code": "voice_chat.session",
            },
            {
                "client_event_id": "event-2",
                "event_type": "heartbeat",
                "occurred_at": "2026-07-01T10:00:30+08:00",
                "event_date": "2026-07-01",
                "page_path": "/pages/voice-chat/index",
                "feature_code": "voice_chat.session",
                "duration_ms": 30000,
            },
            {
                "client_event_id": "event-3",
                "event_type": "page_leave",
                "occurred_at": "2026-07-01T10:01:50+08:00",
                "event_date": "2026-07-01",
                "page_path": "/pages/voice-chat/index",
                "feature_code": "voice_chat.session",
                "duration_ms": 90000,
                "exit_reason": "app_background",
            },
        ],
    }


async def make_current_user_admin(test_db) -> User:
    user = await test_db.scalar(select(User).where(User.email == "tester@example.com"))
    assert user is not None
    user.role = "admin"
    await test_db.commit()
    await test_db.refresh(user)
    return user


async def test_audit_ingest_records_session_events_and_daily_rollups(client, test_db):
    response = await client.post("/api/audit/events", json=audit_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 3
    assert body["duplicate"] == 0

    duplicate = await client.post("/api/audit/events", json=audit_payload())
    assert duplicate.status_code == 200
    assert duplicate.json()["accepted"] == 0
    assert duplicate.json()["duplicate"] == 3

    sessions = (await test_db.execute(select(AuditSession))).scalars().all()
    events = (await test_db.execute(select(AuditEvent))).scalars().all()
    assert len(sessions) == 1
    assert len(events) == 3
    assert sessions[0].active_ms == 90_000
    assert sessions[0].exit_page_path == "/pages/voice-chat/index"
    assert sessions[0].exit_feature_code == "voice_chat.session"
    assert sessions[0].exit_reason == "app_background"

    daily_user = await test_db.scalar(select(AuditDailyUserUsage))
    assert daily_user is not None
    assert daily_user.active_ms == 90_000
    assert daily_user.session_count == 1
    assert daily_user.event_count == 3
    assert daily_user.last_exit_page_path == "/pages/voice-chat/index"

    daily_feature = await test_db.scalar(select(AuditDailyFeatureUsage))
    assert daily_feature is not None
    assert daily_feature.feature_code == "voice_chat.session"
    assert daily_feature.active_ms == 90_000
    assert daily_feature.event_count == 3


async def test_audit_late_older_page_leave_does_not_replace_latest_exit(client, test_db):
    response = await client.post("/api/audit/events", json=audit_payload())
    assert response.status_code == 200

    late_leave = {
        "client_session_id": "client-session-1",
        "platform": "miniprogram",
        "events": [
            {
                "client_event_id": "late-leave",
                "event_type": "page_leave",
                "occurred_at": "2026-07-01T10:00:10+08:00",
                "event_date": "2026-07-01",
                "page_path": "/pages/home/index",
                "feature_code": "home.view",
                "duration_ms": 1000,
                "exit_reason": "navigate",
            },
        ],
    }
    late_response = await client.post("/api/audit/events", json=late_leave)
    assert late_response.status_code == 200
    assert late_response.json()["accepted"] == 1

    session = await test_db.scalar(select(AuditSession))
    assert session is not None
    assert session.active_ms == 91_000
    assert session.exit_page_path == "/pages/voice-chat/index"
    assert session.exit_feature_code == "voice_chat.session"
    assert session.exit_reason == "app_background"


async def test_audit_dashboard_requires_admin(client):
    response = await client.get("/api/audit/dashboard", params={"from": "2026-07-01", "to": "2026-07-01"})

    assert response.status_code == 403


async def test_admin_can_query_audit_dashboard_and_user_day(client, test_db):
    await client.post("/api/audit/events", json=audit_payload())
    admin = await make_current_user_admin(test_db)

    dashboard = await client.get(
        "/api/audit/dashboard",
        params={"from": "2026-07-01", "to": "2026-07-01", "platform": "miniprogram"},
    )
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["summary"] == {
        "active_ms": 90000,
        "session_count": 1,
        "event_count": 3,
        "user_count": 1,
        "top_exit_page_path": "/pages/voice-chat/index",
        "top_exit_feature_code": "voice_chat.session",
    }
    assert body["daily_users"][0]["user"]["id"] == admin.id
    assert body["daily_users"][0]["last_exit_reason"] == "app_background"
    assert body["feature_usage"][0]["feature_code"] == "voice_chat.session"
    assert body["feature_usage"][0]["active_ms"] == 90000
    assert body["exit_locations"][0] == {
        "page_path": "/pages/voice-chat/index",
        "feature_code": "voice_chat.session",
        "reason": "app_background",
        "count": 1,
    }

    detail = await client.get(f"/api/audit/users/{admin.id}/days/2026-07-01")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["user"]["id"] == admin.id
    assert len(detail_body["sessions"]) == 1
    assert detail_body["sessions"][0]["active_ms"] == 90000
    assert [event["client_event_id"] for event in detail_body["sessions"][0]["events"]] == [
        "event-1",
        "event-2",
        "event-3",
    ]
