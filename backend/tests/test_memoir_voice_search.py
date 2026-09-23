import asyncio
import base64

import pytest

from app.services.ai_base import AITextServiceBase
from app.services.asr.streaming import StreamingASREvent
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

    monkeypatch.setattr(
        "app.routers.memoir.voice.memoir_voice_search.transcribe_voice_search_audio",
        fake_transcribe,
    )

    response = await client.post(
        "/api/mobile/memoir/search-query/transcribe",
        json={"audio_base64": base64.b64encode(b"pcm").decode("ascii"), "mime_type": "audio/pcm"},
    )

    assert response.status_code == 200
    assert response.json() == {"transcript": "外婆家的院子"}
    assert calls == [b"pcm"]


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
            "guidance_mode": "guided_interview",
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
    assert parsed.filters.guidance_mode == "guided_interview"
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
        "filters": {"included": "included", "guidance_mode": None, "module": "place"},
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
