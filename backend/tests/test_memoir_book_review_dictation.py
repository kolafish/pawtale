from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from app.routers import dictation_gateway
from app.routers.memoir import dictation as dictation_router


async def test_memoir_project_dictation_streams_for_owned_project(
    test_db,
    monkeypatch,
):
    user = SimpleNamespace(id="user-1")
    calls: dict[str, Any] = {}

    async def authenticate(_websocket, _db):
        return user

    async def available(_db, **kwargs):
        calls["availability"] = kwargs
        return True

    class Lease:
        released = False

        async def release(self):
            if self.released:
                return
            self.released = True
            calls["released"] = True

    lease = Lease()

    class CapacityManager:
        async def acquire(self, user_id, session_id):
            calls["capacity"] = (user_id, session_id)
            return lease

    class GatewaySession:
        def __init__(self, **kwargs):
            calls["gateway"] = kwargs

        async def run(self):
            await calls["gateway"]["on_stop"](None, "client_stop")
            calls["released_before_stopped"] = lease.released
            calls["ran"] = True

    def create_asr(user_id, session_id):
        calls["asr"] = (user_id, session_id)
        return object()

    monkeypatch.setattr(dictation_router, "authenticate_websocket", authenticate)

    async def credit_available(_user_id):
        return None

    monkeypatch.setattr(dictation_gateway, "assert_ai_credit_available", credit_available)

    async def authorized(_db, _user_id):
        return True

    monkeypatch.setattr(dictation_router, "has_current_voice_authorization_consent", authorized)
    monkeypatch.setattr(
        dictation_router,
        "_memoir_project_dictation_is_available",
        available,
    )
    monkeypatch.setattr(
        dictation_gateway,
        "voice_gateway_capacity_manager",
        CapacityManager(),
    )
    monkeypatch.setattr(dictation_gateway, "VoiceGatewaySession", GatewaySession)
    monkeypatch.setattr(
        dictation_gateway,
        "create_realtime_volc_streaming_asr_client",
        create_asr,
    )

    websocket = FakeWebSocket()
    await dictation_router.stream_memoir_project_dictation(
        websocket,
        "project-1",
        test_db,
    )

    assert websocket.accepted
    assert calls["ran"] and calls["released"]
    assert calls["released_before_stopped"] is True
    assert calls["availability"] == {
        "project_id": "project-1",
        "current_user": user,
    }
    assert calls["capacity"][0] == "user-1"
    assert calls["capacity"][1].startswith("memoir-project-dictation:")
    assert calls["gateway"]["diagnostics_context"] == {
        "gateway": "memoir_project_dictation",
        "project_id": "project-1",
    }
    assert calls["gateway"]["text_normalizer"]("把第一张放到第二张后面") == ("把第一章放到第二章后面")
    assert callable(calls["gateway"]["authorize_audio"])
    calls["gateway"]["asr_client_factory"]()
    assert calls["asr"] == calls["capacity"]


async def test_memoir_project_dictation_rejects_unavailable_project(
    test_db,
    monkeypatch,
):
    async def authenticate(_websocket, _db):
        return SimpleNamespace(id="user-1")

    async def unavailable(_db, **_kwargs):
        return False

    monkeypatch.setattr(dictation_router, "authenticate_websocket", authenticate)
    async def authorized(_db, _user_id):
        return True
    monkeypatch.setattr(dictation_router, "has_current_voice_authorization_consent", authorized)
    monkeypatch.setattr(
        dictation_router,
        "_memoir_project_dictation_is_available",
        unavailable,
    )
    websocket = FakeWebSocket()

    await dictation_router.stream_memoir_project_dictation(
        websocket,
        "project-1",
        test_db,
    )

    assert websocket.sent == [
        {
            "type": "session.error",
            "state": "failed",
            "error": "memoir_project_dictation_unavailable",
        }
    ]
    assert websocket.closed_with == 1008


async def test_memoir_project_dictation_requires_owned_project(
    test_db,
    monkeypatch,
):
    user = SimpleNamespace(id="user-1")
    calls: dict[str, Any] = {}

    async def load_project(_db, project_id, user_id, *, allow_admin):
        calls["project"] = (project_id, user_id, allow_admin)

    monkeypatch.setattr(dictation_router, "is_admin_user", lambda _user: False)
    monkeypatch.setattr(dictation_router, "load_memoir_project", load_project)

    assert await dictation_router._memoir_project_dictation_is_available(
        test_db,
        project_id="project-1",
        current_user=user,
    )
    assert calls["project"] == ("project-1", "user-1", False)

    async def missing_project(*_args, **_kwargs):
        raise HTTPException(status_code=404)

    monkeypatch.setattr(dictation_router, "load_memoir_project", missing_project)
    assert not await dictation_router._memoir_project_dictation_is_available(
        test_db,
        project_id="project-1",
        current_user=user,
    )


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None
        self.cookies: dict[str, str] = {}

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000):
        self.closed_with = code
