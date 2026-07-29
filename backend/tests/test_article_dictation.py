from typing import Any

from app.models.users import User
from app.routers.projects import dictation as dictation_router
from app.services.asr.gateway import VoiceGatewaySession
from app.services.asr.streaming import StreamingASREvent
from project_helpers import create_project


def test_shared_gateway_preserves_article_dictation_text():
    session = VoiceGatewaySession(
        client=FakeWebSocket(),
        asr_client_factory=lambda: object(),  # type: ignore[return-value]
    )

    payload = session._asr_event_payload(
        StreamingASREvent(type="partial", text="我采访了张杰。"),
        event_type="asr.partial",
    )

    assert payload["text"] == "我采访了张杰。"


async def test_article_dictation_accepts_only_owned_article_document(
    client,
    test_db,
    monkeypatch,
):
    project = await create_project(client, "口述文章", project_type="article")
    document_id = project["groups"][0]["documents"][0]["id"]
    user_id = (await client.get("/api/auth/me")).json()["id"]
    user = await test_db.get(User, user_id)
    calls: dict[str, Any] = {}

    async def authenticate(_websocket, _db):
        return user

    class Lease:
        async def release(self):
            calls["released"] = True

    class CapacityManager:
        async def acquire(self, current_user_id, session_id):
            calls["capacity"] = (current_user_id, session_id)
            return Lease()

    class GatewaySession:
        def __init__(self, **kwargs):
            calls["gateway"] = kwargs

        async def run(self):
            calls["ran"] = True

    def create_asr(current_user_id, session_id):
        calls["asr"] = (current_user_id, session_id)
        return object()

    monkeypatch.setattr(dictation_router, "authenticate_websocket", authenticate)
    monkeypatch.setattr(dictation_router, "voice_gateway_capacity_manager", CapacityManager())
    monkeypatch.setattr(dictation_router, "VoiceGatewaySession", GatewaySession)
    monkeypatch.setattr(dictation_router, "create_realtime_volc_streaming_asr_client", create_asr)

    websocket = FakeWebSocket()
    await dictation_router.stream_article_dictation(
        websocket,
        project["id"],
        document_id,
        test_db,
    )

    assert websocket.accepted
    assert calls["ran"] and calls["released"]
    assert calls["capacity"][0] == user_id
    assert calls["capacity"][1].startswith("article-dictation:")
    assert calls["gateway"]["diagnostics_context"] == {
        "gateway": "article_dictation",
        "project_id": project["id"],
        "document_id": document_id,
    }
    assert "on_audio_frame" not in calls["gateway"]
    assert "on_asr_final" not in calls["gateway"]
    calls["gateway"]["asr_client_factory"]()
    assert calls["asr"] == calls["capacity"]
    assert not await dictation_router._article_document_is_available(
        test_db,
        project_id=project["id"],
        document_id=document_id,
        user_id="another-user",
    )


async def test_article_dictation_rejects_non_article_project(client, test_db, monkeypatch):
    project = await create_project(client, "小说", project_type="novel")
    document_id = project["groups"][0]["documents"][0]["id"]
    user_id = (await client.get("/api/auth/me")).json()["id"]
    user = await test_db.get(User, user_id)

    async def authenticate(_websocket, _db):
        return user

    monkeypatch.setattr(dictation_router, "authenticate_websocket", authenticate)
    websocket = FakeWebSocket()

    await dictation_router.stream_article_dictation(
        websocket,
        project["id"],
        document_id,
        test_db,
    )

    assert websocket.sent == [
        {
            "type": "session.error",
            "state": "failed",
            "error": "article_dictation_unavailable",
        }
    ]
    assert websocket.closed_with == 1008


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
