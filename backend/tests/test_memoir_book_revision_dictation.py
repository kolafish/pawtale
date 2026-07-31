from types import SimpleNamespace
from typing import Any

from app.domain.memoir.types import MemoirBookRevisionProposalStatus
from app.routers.memoir import book_reviews as book_reviews_router


async def test_book_revision_dictation_streams_for_available_proposal(
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
        async def release(self):
            calls["released"] = True

    class CapacityManager:
        async def acquire(self, user_id, session_id):
            calls["capacity"] = (user_id, session_id)
            return Lease()

    class GatewaySession:
        def __init__(self, **kwargs):
            calls["gateway"] = kwargs

        async def run(self):
            calls["ran"] = True

    def create_asr(user_id, session_id):
        calls["asr"] = (user_id, session_id)
        return object()

    monkeypatch.setattr(book_reviews_router, "authenticate_websocket", authenticate)
    monkeypatch.setattr(
        book_reviews_router,
        "_book_revision_dictation_is_available",
        available,
    )
    monkeypatch.setattr(
        book_reviews_router,
        "voice_gateway_capacity_manager",
        CapacityManager(),
    )
    monkeypatch.setattr(book_reviews_router, "VoiceGatewaySession", GatewaySession)
    monkeypatch.setattr(
        book_reviews_router,
        "create_realtime_volc_streaming_asr_client",
        create_asr,
    )

    websocket = FakeWebSocket()
    await book_reviews_router.stream_memoir_book_revision_dictation(
        websocket,
        "project-1",
        "proposal-1",
        test_db,
    )

    assert websocket.accepted
    assert calls["ran"] and calls["released"]
    assert calls["availability"] == {
        "project_id": "project-1",
        "proposal_id": "proposal-1",
        "current_user": user,
    }
    assert calls["capacity"][0] == "user-1"
    assert calls["capacity"][1].startswith("book-revision-dictation:")
    assert calls["gateway"]["diagnostics_context"] == {
        "gateway": "book_revision_dictation",
        "project_id": "project-1",
        "proposal_id": "proposal-1",
    }
    assert calls["gateway"]["text_normalizer"]("把第一张放到第二张后面") == ("把第一章放到第二章后面")
    calls["gateway"]["asr_client_factory"]()
    assert calls["asr"] == calls["capacity"]


async def test_book_revision_dictation_rejects_unavailable_proposal(
    test_db,
    monkeypatch,
):
    async def authenticate(_websocket, _db):
        return SimpleNamespace(id="user-1")

    async def unavailable(_db, **_kwargs):
        return False

    monkeypatch.setattr(book_reviews_router, "authenticate_websocket", authenticate)
    monkeypatch.setattr(
        book_reviews_router,
        "_book_revision_dictation_is_available",
        unavailable,
    )
    websocket = FakeWebSocket()

    await book_reviews_router.stream_memoir_book_revision_dictation(
        websocket,
        "project-1",
        "proposal-1",
        test_db,
    )

    assert websocket.sent == [
        {
            "type": "session.error",
            "state": "failed",
            "error": "book_revision_dictation_unavailable",
        }
    ]
    assert websocket.closed_with == 1008


async def test_book_revision_dictation_requires_owned_adjustable_proposal(
    test_db,
    monkeypatch,
):
    user = SimpleNamespace(id="user-1")
    proposal = object()
    calls: dict[str, Any] = {}
    response = SimpleNamespace(
        status=MemoirBookRevisionProposalStatus.READY,
        can_dismiss=True,
    )

    async def load_project(_db, project_id, user_id, *, allow_admin):
        calls["project"] = (project_id, user_id, allow_admin)

    async def load_proposal(_db, **kwargs):
        calls["proposal"] = kwargs
        return proposal

    async def proposal_response(_db, **kwargs):
        calls["response"] = kwargs
        return response

    monkeypatch.setattr(book_reviews_router, "is_admin_user", lambda _user: False)
    monkeypatch.setattr(book_reviews_router, "load_memoir_project", load_project)
    monkeypatch.setattr(
        book_reviews_router.book_revision_proposals,
        "load_proposal",
        load_proposal,
    )
    monkeypatch.setattr(
        book_reviews_router.book_revision_proposals,
        "proposal_response",
        proposal_response,
    )

    assert await book_reviews_router._book_revision_dictation_is_available(
        test_db,
        project_id="project-1",
        proposal_id="proposal-1",
        current_user=user,
    )
    assert calls["project"] == ("project-1", "user-1", False)
    assert calls["proposal"] == {
        "project_id": "project-1",
        "proposal_id": "proposal-1",
    }
    assert calls["response"] == {
        "proposal": proposal,
        "preview": "none",
    }

    response.status = MemoirBookRevisionProposalStatus.STALE
    assert not await book_reviews_router._book_revision_dictation_is_available(
        test_db,
        project_id="project-1",
        proposal_id="proposal-1",
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
