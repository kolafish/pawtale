import asyncio
import base64

import pytest

from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.voice_search import (
    MAX_VOICE_SEARCH_AUDIO_BYTES,
    VoiceSearchTranscriptionError,
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

    async def fake_transcribe(audio: bytes, *, asr_client_factory):
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
