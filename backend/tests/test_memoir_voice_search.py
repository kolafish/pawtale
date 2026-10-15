import asyncio
import base64
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.application_errors import InsufficientAICreditError
from app.auth import get_current_user
from app.database import get_db
from app.services.ai_base import AITextServiceBase
from app.services.asr.streaming import StreamingASREvent
from app.schemas.memoir_voice import MemoirVoiceSearchTranscriptionRequest
from app.routers.memoir.voice import router as memoir_voice_router, transcribe_mobile_memoir_search_query
from app.services.memoir.voice_authorization import CURRENT_VOICE_AUTHORIZATION_VERSION
from app.services.memoir.voice_search import (
    MAX_VOICE_SEARCH_AUDIO_BYTES,
    ParsedVoiceSearchQuery,
    VoiceSearchQueryFilters,
    VoiceSearchTranscriptionError,
    parse_voice_search_query,
    transcribe_voice_search_audio,
)


async def test_transcribe_voice_search_audio_returns_latest_final_text():
    asr_client = FakeSearchASRClient(final_texts=["外婆", "外婆家的院子"])

    transcript = await transcribe_voice_search_audio(b"pcm", asr_client_factory=lambda: asr_client)

    assert transcript == "外婆家的院子"
    assert asr_client.started
    assert asr_client.sent_audio == [b"pcm"]
    assert asr_client.finished
    assert asr_client.closed


async def test_transcribe_voice_search_audio_rejects_empty_audio():
    with pytest.raises(VoiceSearchTranscriptionError) as exc_info:
        await transcribe_voice_search_audio(b"", asr_client_factory=FakeSearchASRClient)

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "voice_search_audio_empty"


async def test_transcribe_voice_search_audio_rejects_oversized_audio():
    with pytest.raises(VoiceSearchTranscriptionError) as exc_info:
        await transcribe_voice_search_audio(
            b"x" * (MAX_VOICE_SEARCH_AUDIO_BYTES + 1),
            asr_client_factory=FakeSearchASRClient,
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.code == "voice_search_audio_too_large"


async def test_transcribe_voice_search_audio_maps_provider_error():
    asr_client = FakeSearchASRClient(error="asr_provider_forbidden")

    with pytest.raises(VoiceSearchTranscriptionError) as exc_info:
        await transcribe_voice_search_audio(b"pcm", asr_client_factory=lambda: asr_client)

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "asr_provider_forbidden"
    assert asr_client.closed


async def test_transcribe_mobile_memoir_search_query_route(client, monkeypatch):
    calls: list[bytes] = []

    async def fake_transcribe(audio: bytes, *, asr_client_factory, mime_type: str):
        calls.append(audio)
        return "外婆家的院子"

    async def allow_credit(_user_id: str) -> None:
        return None

    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_search.transcribe_voice_search_audio",
        fake_transcribe,
    )
    monkeypatch.setattr("app.routers.memoir.voice.assert_ai_credit_available", allow_credit)
    assert (await client.post(
        "/api/mobile/memoir/voice-authorization-consent",
        json={"version": CURRENT_VOICE_AUTHORIZATION_VERSION, "agreed": True},
    )).status_code == 201

    response = await client.post(
        "/api/mobile/memoir/search-query/transcribe",
        json={"audio_base64": base64.b64encode(b"pcm").decode("ascii"), "mime_type": "audio/pcm"},
    )

    assert response.status_code == 200
    assert response.json() == {"transcript": "外婆家的院子"}
    assert calls == [b"pcm"]


async def test_search_transcription_http_rejects_revoked_voice_consent(monkeypatch):
    async def no_consent(_db, _user_id: str) -> bool:
        return False

    async def should_not_run(*_args, **_kwargs):
        raise AssertionError("ASR or credit check started without voice consent")

    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_authorization.has_current_voice_authorization_consent",
        no_consent,
    )
    monkeypatch.setattr("app.routers.memoir.voice.assert_ai_credit_available", should_not_run)
    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_search.transcribe_voice_search_audio",
        should_not_run,
    )
    async def fake_db():
        return object()

    async def fake_user():
        return SimpleNamespace(id="speaker")

    app = FastAPI()
    app.include_router(memoir_voice_router, prefix="/api/mobile/memoir")
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/mobile/memoir/search-query/transcribe",
            json={"audio_base64": base64.b64encode(b"pcm").decode("ascii"), "mime_type": "audio/pcm"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "请先同意语音授权"


async def test_search_transcription_rejects_exhausted_credit_before_asr(monkeypatch):
    async def has_consent(_db, _user_id: str) -> bool:
        return True

    async def no_credit(_user_id: str) -> None:
        raise InsufficientAICreditError("AI 额度已用尽")

    async def should_not_transcribe(*_args, **_kwargs):
        raise AssertionError("ASR started without AI credit")

    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_authorization.has_current_voice_authorization_consent",
        has_consent,
    )
    monkeypatch.setattr("app.routers.memoir.voice.assert_ai_credit_available", no_credit)
    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_search.transcribe_voice_search_audio",
        should_not_transcribe,
    )
    request = MemoirVoiceSearchTranscriptionRequest(
        audio_base64=base64.b64encode(b"pcm").decode("ascii"), mime_type="audio/pcm",
    )

    with pytest.raises(InsufficientAICreditError):
        await transcribe_mobile_memoir_search_query(
            request, db=object(), current_user=SimpleNamespace(id="speaker"),
        )


async def test_search_transcription_checks_consent_and_credit_before_asr(monkeypatch):
    calls = []

    async def has_consent(_db, user_id: str) -> bool:
        calls.append(("consent", user_id))
        return True

    async def allow_credit(user_id: str) -> None:
        calls.append(("credit", user_id))

    async def transcribe(audio: bytes, *, asr_client_factory, mime_type: str) -> str:
        calls.append(("asr", audio, mime_type))
        return "外婆家的院子"

    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_authorization.has_current_voice_authorization_consent",
        has_consent,
    )
    monkeypatch.setattr("app.routers.memoir.voice.assert_ai_credit_available", allow_credit)
    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_search.transcribe_voice_search_audio",
        transcribe,
    )
    request = MemoirVoiceSearchTranscriptionRequest(
        audio_base64=base64.b64encode(b"pcm").decode("ascii"), mime_type="audio/pcm",
    )

    response = await transcribe_mobile_memoir_search_query(
        request, db=object(), current_user=SimpleNamespace(id="speaker"),
    )

    assert response.transcript == "外婆家的院子"
    assert calls == [
        ("consent", "speaker"),
        ("credit", "speaker"),
        ("asr", b"pcm", "audio/pcm"),
    ]


async def test_transcribe_mobile_memoir_search_query_rejects_invalid_base64(client):
    response = await client.post(
        "/api/mobile/memoir/search-query/transcribe",
        json={"audio_base64": "not-base64", "mime_type": "audio/pcm"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "搜索语音格式无效"


async def test_parse_voice_search_query_uses_ai_json_payload():
    ai_client = FakeAIClient(
        """```json
        {
          "query": "外婆 院子",
          "keywords": ["外婆", "院子"],
          "filters": {
            "included": "not_included",
            "narration_mode": "ai_interview",
            "module": "place"
          }
        }
        ```"""
    )

    parsed = await parse_voice_search_query(
        "帮我找还没写进书的外婆家院子，最好是引导访谈里地点相关的",
        ai_client=ai_client,
    )

    assert parsed.used_ai is True
    assert parsed.query == "外婆 院子"
    assert parsed.keywords == ["外婆", "院子"]
    assert parsed.filters.included == "not_included"
    assert parsed.filters.narration_mode == "ai_interview"
    assert parsed.filters.module == "place"
    assert parsed.provider == "fake-ai"
    assert parsed.model == "fake-model"


async def test_parse_voice_search_query_falls_back_when_ai_fails():
    parsed = await parse_voice_search_query(
        "帮我找未收录的外婆家院子，地点相关",
        ai_client=FailingAIClient(),
    )

    assert parsed.used_ai is False
    assert parsed.query == "外婆家院子"
    assert parsed.keywords == ["外婆家院子"]
    assert parsed.filters.included == "not_included"
    assert parsed.filters.module == "place"


async def test_parse_voice_search_query_invalid_ai_payload_uses_fallback_without_broad_person_filter():
    parsed = await parse_voice_search_query(
        "帮我找人民公社食堂",
        ai_client=FakeAIClient("不是 JSON"),
    )

    assert parsed.used_ai is False
    assert parsed.provider is None
    assert parsed.query == "人民公社食堂"
    assert parsed.filters.module is None


async def test_parse_voice_search_query_fallback_allows_filter_only_search():
    parsed = await parse_voice_search_query(
        "帮我找工作相关的未收录记录",
        ai_client=FakeAIClient("不是 JSON"),
    )

    assert parsed.used_ai is False
    assert parsed.query == ""
    assert parsed.keywords == []
    assert parsed.filters.included == "not_included"
    assert parsed.filters.module == "work"
    assert parsed.display_text == "未收录 · 工作"


async def test_parse_mobile_memoir_search_query_route(client, monkeypatch):
    calls: list[dict[str, str | None]] = []

    async def fake_parse(text: str, *, ai_model: str | None = None):
        calls.append({"text": text, "ai_model": ai_model})
        return ParsedVoiceSearchQuery(
            original_text=text,
            query="外婆 院子",
            keywords=["外婆", "院子"],
            filters=VoiceSearchQueryFilters(included="included", guidance_mode=None, module="place"),
            display_text="外婆 院子 · 已收录 · 地点",
            used_ai=True,
            provider="fake-ai",
            model=ai_model,
        )

    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_search.parse_voice_search_query",
        fake_parse,
    )

    response = await client.post(
        "/api/mobile/memoir/search-query/parse",
        json={"text": "帮我找外婆家院子", "ai_model": "deepseek"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "original_text": "帮我找外婆家院子",
        "query": "外婆 院子",
        "keywords": ["外婆", "院子"],
        "filters": {"included": "included", "guidance_mode": None, "narration_mode": None, "module": "place"},
        "display_text": "外婆 院子 · 已收录 · 地点",
        "used_ai": True,
        "provider": "fake-ai",
        "model": "deepseek",
    }
    assert calls == [{"text": "帮我找外婆家院子", "ai_model": "deepseek"}]


class FakeSearchASRClient:
    def __init__(self, final_texts: list[str] | None = None, error: str = "") -> None:
        self.final_texts = final_texts or ["帮我找外婆家的院子"]
        self.error = error
        self.started = False
        self.finished = False
        self.closed = False
        self.sent_audio: list[bytes] = []
        self.events_queue: asyncio.Queue[StreamingASREvent | None] = asyncio.Queue()

    async def start(self) -> None:
        self.started = True

    async def send_audio(self, audio: bytes) -> None:
        self.sent_audio.append(audio)

    async def finish(self) -> None:
        self.finished = True
        if self.error:
            await self.events_queue.put(StreamingASREvent(type="error", error=self.error))
        else:
            for text in self.final_texts:
                await self.events_queue.put(StreamingASREvent(type="final", text=text))
        await self.events_queue.put(None)

    async def events(self):
        while True:
            event = await self.events_queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self.closed = True


class FakeAIClient(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-model"

    def __init__(self, content: str) -> None:
        self.content = content

    async def _call_llm(self, *args, **kwargs):
        return self.content


class FailingAIClient(FakeAIClient):
    def __init__(self) -> None:
        super().__init__("")

    async def _call_llm(self, *args, **kwargs):
        raise RuntimeError("provider unavailable")


@pytest.mark.parametrize("word", ["AI访谈", "AI 访谈", "自由讲述", "引导访谈", "主题访谈"])
async def test_legacy_search_names_select_all_ordinary_ai_interviews(word):
    parsed = await parse_voice_search_query(f"帮我找{word}记录", ai_client=FailingAIClient())
    assert getattr(parsed.filters, "narration_mode", None) == "ai_interview"
    assert parsed.filters.guidance_mode is None
    assert parsed.query == ""
    assert parsed.display_text == "AI 访谈"


async def test_search_modes_retrieve_legacy_and_new_ai_without_human_or_completion_leaks(client, test_db):
    import json

    from app.models.memoir import MemoirVoiceSession
    from app.services.memoir.voice_transcripts import list_voice_transcripts
    from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project

    project_id = await ensure_mobile_memoir_project(client)
    records = []
    for guidance, interview, goal in [
        ("free_narration", "ai_interview", "open_narration"),
        ("guided_interview", "ai_interview", "open_narration"),
        ("free_narration", "human_interview", "open_narration"),
        ("guided_interview", "ai_interview", "book_completion"),
    ]:
        record = await create_test_voice_transcript(client, test_db, "外婆追求自由，老师耐心引导。", project_id=project_id)
        stored = await test_db.get(MemoirVoiceSession, record["session"]["id"])
        # Explicit DB fixtures preserve historical values instead of using normalized creation inputs.
        stored.guidance_mode, stored.interview_type, stored.session_goal = guidance, interview, goal
        await test_db.commit()
        records.append(record)
    ids = [record["utterance_id"] for record in records]
    other_project = (await client.post("/api/mobile/memoir/projects", json={"title": "另一项目"})).json()["id"]
    await create_test_voice_transcript(client, test_db, "外婆追求自由，老师耐心引导。", project_id=other_project)

    cases = [
        ("AI 访谈", "ai_interview", "", ids[:2]),
        ("自由讲述", "ai_interview", "", ids[:2]),
        ("引导访谈", "ai_interview", "", ids[:2]),
        ("主题访谈", "ai_interview", "", ids[:2]),
        ("真人访谈", "human_interview", "", ids[2:3]),
        ("书稿补充", "book_completion", "", ids[3:]),
        ("外婆", None, "外婆", ids),
        ("自由", None, "自由", ids),
        ("引导", None, "引导", ids),
    ]
    for phrase, mode, query, expected in cases:
        # Fixed offline structured outputs and provider-failure parsing must lead to the same actual rows.
        output = json.dumps({"query": query, "keywords": [query] if query else [], "filters": {"narration_mode": mode}})
        echoed = json.dumps({"query": f"{phrase}记录", "keywords": [f"{phrase}记录"], "filters": {"narration_mode": mode}})
        for ai_client in (FakeAIClient(output), FakeAIClient(echoed), FailingAIClient()):
            parsed = await parse_voice_search_query(f"帮我找{phrase}记录", ai_client=ai_client)
            assert parsed.filters.narration_mode == mode
            assert parsed.filters.guidance_mode is None
            assert parsed.query == query
            params = {"project_id": project_id, "q": parsed.query, "limit": 1}
            if parsed.filters.narration_mode:
                params["narration_mode"] = mode
            found = []
            for offset in range(len(expected)):
                response = await client.get("/api/mobile/memoir/voice-transcripts", params={**params, "offset": offset})
                assert response.status_code == 200
                payload = response.json()
                assert payload["total"] == len(expected)
                found.extend(item["id"] for item in payload["items"])
            assert set(found) == set(expected)
    for mode, expected in [("free_narration", [ids[0], ids[2]]), ("guided_interview", [ids[1], ids[3]])]:
        response = await client.get("/api/mobile/memoir/voice-transcripts", params={"project_id": project_id, "guidance_mode": mode})
        assert {item["id"] for item in response.json()["items"]} == set(expected)
    private = await list_voice_transcripts(test_db, "another-user", project_id=project_id, narration_mode="ai_interview")
    assert private.total == 0
    assert private.items == []
    legacy = await test_db.get(MemoirVoiceSession, records[0]["session"]["id"])
    await test_db.refresh(legacy)
    assert legacy.guidance_mode == "free_narration"


async def test_model_story_keywords_do_not_lose_command_substrings():
    parsed = await parse_voice_search_query(
        "帮我找寻找自由的故事", ai_client=FakeAIClient('{"query":"寻找自由","keywords":["寻找自由"],"filters":{}}'),
    )
    assert parsed.query == "寻找自由"
    assert parsed.keywords == ["寻找自由"]
    assert parsed.filters.narration_mode is None
